"""Task-scoped adapter for the existing Chaoxing learning engine.

The legacy command-line program keeps its learning algorithm in :mod:`main`.
This module supplies the account/task values owned by ``TaskManager`` and
translates the engine's callbacks into the task reporter API.  It deliberately
does not implement a second course/chapter/job processor.
"""

from __future__ import annotations

import copy
import inspect
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from api.base import build_session
from api.cookies import account_cookie_path, save_cookie_file
from api.live_process import StudyCancelled
from api.vision_ocr import vision_ocr_context

from .answer_connection import normalize_completion_url, outbound_url
from .task_manager import StudyRunContext, _secret_values as _context_secret_values


class StudyRunError(RuntimeError):
    """Raised when account-specific study setup or processing fails."""

    def __init__(self, message: str, *, code: str = "study_run_error") -> None:
        super().__init__(message)
        self.code = code


class CourseSelectionInvalid(StudyRunError):
    """Raised when an explicit Web task course ID is absent from the catalog."""

    def __init__(self, message: str = "course_selection_invalid") -> None:
        super().__init__(message, code="course_selection_invalid")


@dataclass(frozen=True, repr=False)
class EngineFactoryRequest:
    """Values supplied to an injected study-engine factory.

    The request is useful for tests and integrations that prefer one
    positional value.  The default factory accepts the same fields as
    keywords.  Its representation intentionally avoids printing credentials
    or complete configuration mappings.
    """

    context: StudyRunContext
    auth: Any
    course_ids: list[str]
    common_config: dict[str, Any]
    tiku_config: dict[str, Any]
    answer_semaphore: Any
    session: Any
    cookie_update_callback: Callable[[Mapping[str, str]], None]

    def __repr__(self) -> str:
        return (
            "EngineFactoryRequest("
            f"task_id={self.context.task_id!r}, account_id={self.context.account_id!r}, "
            f"course_ids={self.course_ids!r}, auth=<redacted>, "
            "common_config=<redacted>, tiku_config=<redacted>, "
            f"answer_semaphore={self.answer_semaphore!r}, session=<account-session>)"
        )


def _default_engine_factory(
    *,
    common_config: Mapping[str, Any],
    tiku_config: Mapping[str, Any],
    answer_semaphore=None,
    session=None,
    cookie_update_callback=None,
    **_ignored: Any,
):
    """Construct a Chaoxing client through the legacy initialization path."""

    # Import lazily so importing the Web application does not eagerly start
    # CLI configuration or pull in optional command-line dependencies.
    import main

    init_values = {"answer_semaphore": answer_semaphore}
    signature = _callable_signature(main.init_chaoxing)
    if signature is None or "session" in signature.parameters:
        init_values["session"] = session
    if signature is None or "cookie_update_callback" in signature.parameters:
        init_values["cookie_update_callback"] = cookie_update_callback
    return main.init_chaoxing(dict(common_config), dict(tiku_config), **init_values)


def _callable_signature(factory: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return inspect.signature(factory)
    except (TypeError, ValueError):
        return None


def _invoke_factory(
    factory: Callable[..., Any],
    values: dict[str, Any],
    request: EngineFactoryRequest,
) -> Any:
    """Call an injected factory across the small compatibility surface.

    Production uses the keyword form.  Supporting a one-argument request and
    a positional ``(common_config, tiku_config)`` form keeps test doubles and
    older integrations straightforward without catching a ``TypeError``
    raised *inside* the factory itself.
    """

    signature = _callable_signature(factory)
    if signature is None:
        return factory(**values)

    parameters = signature.parameters
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return factory(**values)

    accepted = {
        name: value
        for name, value in values.items()
        if name in parameters
        and parameters[name].kind
        not in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.VAR_POSITIONAL}
    }
    missing_required = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        and parameter.default is inspect.Parameter.empty
        and parameter.name not in accepted
    ]
    if len(missing_required) == 1 and (
        missing_required[0].name in {"request", "engine_request"}
        or not accepted
    ):
        return factory(request)

    # A factory exposing only the legacy initializer's two positional config
    # values is common in small unit-test doubles.
    if not accepted and len(missing_required) >= 2:
        return factory(request.common_config, request.tiku_config)

    return factory(**accepted)


def _invoke_login(engine: Any, *, login_with_cookies: bool) -> Any:
    login = getattr(engine, "login", None)
    if not callable(login):
        return {"status": True}
    signature = _callable_signature(login)
    if signature is not None:
        parameters = signature.parameters
        accepts_keyword = "login_with_cookies" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if not accepts_keyword:
            return login()
    return login(login_with_cookies=login_with_cookies)


def _result_status(result: Any) -> bool:
    if result is None:
        # Test doubles often omit login when setup is intentionally local.
        return True
    if isinstance(result, bool):
        return result
    if isinstance(result, Mapping):
        return bool(result.get("status"))
    status = getattr(result, "status", None)
    return bool(status) if status is not None else True


class ChaoxingStudyRunner:
    """Run one isolated account learning task through ``main.py``."""

    def __init__(
        self,
        data_dir: Path,
        engine_factory: Callable[..., Any] | None = None,
        *,
        cookie_update_callback_factory: Callable[
            [str], Callable[[Mapping[str, str]], None]
        ]
        | None = None,
        running_in_docker: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.engine_factory = engine_factory or _default_engine_factory
        self.cookie_update_callback_factory = cookie_update_callback_factory
        self.running_in_docker = (
            running_in_docker
            if isinstance(running_in_docker, bool)
            else str(running_in_docker).strip().lower()
            in {"1", "true", "yes", "y", "on"}
        )

    @staticmethod
    def _secret_values(context: StudyRunContext) -> tuple[str, ...]:
        return _context_secret_values(
            context.auth,
            context.answer,
            context.preferences.ocr_config,
            context.preferences.notification_config,
        )

    def _safe_error(self, context: StudyRunContext, error: Any) -> str:
        try:
            text = str(error).strip()
        except Exception:
            text = ""
        for secret in self._secret_values(context):
            text = text.replace(secret, "[redacted]")
        return text or "study run failed"

    @staticmethod
    def _report(reporter: Any, method_name: str, *args: Any, **kwargs: Any) -> None:
        method = getattr(reporter, method_name, None)
        if not callable(method):
            return
        # Reporter failures must not turn a successful Chaoxing run into a
        # retryable account failure.  TaskManager's reporter is thread-safe;
        # custom reporters are treated as best-effort integrations here.
        try:
            method(*args, **kwargs)
        except TypeError:
            # A few legacy reporter doubles accept one mapping instead of
            # keyword fields.  Keep that compatibility without changing the
            # TaskReporter contract.
            if kwargs:
                try:
                    method(*args, dict(kwargs))
                except Exception:
                    return
        except Exception:
            return

    def _cookie_callback(
        self, context: StudyRunContext
    ) -> tuple[Any, Callable[[Mapping[str, str]], None]]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        cookie_path = account_cookie_path(
            context.account_id, self.data_dir / "cookies.txt"
        )
        session = build_session(dict(context.auth.cookies))

        callback: Callable[[Mapping[str, str]], None]
        if self.cookie_update_callback_factory is not None:
            callback = self.cookie_update_callback_factory(str(context.account_id))
            if not callable(callback):
                raise TypeError("cookie_update_callback_factory must return a callable")
        else:
            callback = partial(save_cookie_file, path=cookie_path)
        return session, callback

    def _answer_config(self, context: StudyRunContext) -> dict[str, Any]:
        preferences = context.preferences
        answer = context.answer
        base_url = ""
        if answer is not None:
            base_value = getattr(answer, "outbound_base_url", None)
            if base_value is None:
                configured_base_url = getattr(answer, "base_url", "")
                base_value = (
                    outbound_url(configured_base_url, self.running_in_docker)
                    if configured_base_url
                    else ""
                )
            base_url = str(base_value or "")
        endpoint = normalize_completion_url(base_url) if base_url else ""
        return {
            "provider": "AI" if preferences.answer_enabled else "",
            "endpoint": endpoint,
            "key": getattr(answer, "api_key", None) if answer is not None else None,
            "model": getattr(answer, "model", "") if answer is not None else "",
            "cover_rate": preferences.answer_cover_rate,
            "submit": str(preferences.answer_auto_submit).lower(),
            "timeout": getattr(answer, "timeout_seconds", 30.0)
            if answer is not None
            else 30.0,
            "max_retries": getattr(answer, "max_retries", 3)
            if answer is not None
            else 3,
        }

    @staticmethod
    def _course_id(course: Mapping[str, Any]) -> Any:
        return course.get("courseId", course.get("id"))

    @classmethod
    def _course_items(cls, value: Any) -> list[Mapping[str, Any]]:
        """Unwrap the catalog shapes used by clients and test doubles.

        The production decoder returns a list, while account integrations
        commonly wrap it as ``{"courses": [...]}`` or ``{"data": ...}``.
        Keep malformed responses empty so explicit IDs fail with the stable
        selection error instead of accidentally selecting every course.
        """

        if isinstance(value, Mapping):
            for key in ("courses", "course_list", "items"):
                if key in value:
                    return cls._course_items(value[key])
            if "data" in value:
                return cls._course_items(value["data"])
            return []
        if isinstance(value, (list, tuple)):
            return [item for item in value if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _course_title(course: Mapping[str, Any]) -> str:
        return str(course.get("title", course.get("name", "")) or "")

    @staticmethod
    def _chapter_title(point: Mapping[str, Any]) -> str:
        return str(point.get("title", point.get("name", "")) or "")

    @staticmethod
    def _chapter_id(point: Mapping[str, Any]) -> str:
        value = point.get("id", point.get("knowledgeId", point.get("chapterId")))
        return str(value if value is not None else "")

    @staticmethod
    def _job_id(job: Mapping[str, Any]) -> str:
        value = job.get("id", job.get("jobid", job.get("jobId", job.get("_jobid"))))
        return str(value if value is not None else "")

    @staticmethod
    def _job_title(job: Mapping[str, Any]) -> str:
        return str(
            job.get("title", job.get("name", job.get("type", ""))) or ""
        )

    @staticmethod
    def _job_result_status(result: Any) -> str:
        if isinstance(result, str):
            value = result.strip().lower()
            if value in {"completed", "success", "done"}:
                return "completed"
            if value in {"running", "pending", "stopping", "failed", "error"}:
                return value
        is_failure = getattr(result, "is_failure", None)
        if callable(is_failure):
            try:
                return "failed" if is_failure() else "completed"
            except Exception:
                return "failed"
        if isinstance(result, Mapping):
            status = str(result.get("status", result.get("state", ""))).lower()
            if status in {"failed", "error", "invalid"} or result.get("ok") is False:
                return "failed"
        return "completed"

    def run(self, context: StudyRunContext) -> None:
        """Run one account context and report progress to its reporter."""

        if context.cancel_event.is_set():
            raise StudyCancelled()

        preferences = context.preferences
        tiku_config = self._answer_config(context)
        tiku_config["cache_file"] = str(self.data_dir / "answer-cache.json")

        auth_mode = getattr(context.auth, "auth_mode", None)
        use_cookies = bool(context.auth.cookies) if auth_mode is None else auth_mode == "cookies"
        common_config: dict[str, Any] = {
            "use_cookies": use_cookies,
            "username": context.auth.username,
            "password": context.auth.password,
            "course_list": list(context.course_ids),
            "speed": min(2.0, max(1.0, float(preferences.speed))),
            "jobs": preferences.jobs,
            "notopen_action": preferences.notopen_action,
            "task_id": context.task_id,
            "cancel_event": context.cancel_event,
            "ocr_config": dict(preferences.ocr_config),
        }

        counts: dict[str, Any] = {
            "completed_courses": 0,
            "total_courses": 0,
            "completed_chapters": 0,
            "total_chapters": 0,
            "completed_tasks": 0,
            "total_tasks": 0,
        }
        counts_lock = threading.Lock()
        tree_lock = threading.RLock()

        # The runner owns one mutable, reporter-facing tree.  Every count is
        # derived from catalog/point/job callbacks emitted by the real engine;
        # no synthetic ``jobCount=1`` fallback is used.
        course_nodes: list[dict[str, Any]] = []
        course_by_id: dict[str, dict[str, Any]] = {}
        chapter_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        job_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        active_jobs: dict[str, dict[str, Any]] = {}
        completed_courses: set[str] = set()
        completed_chapters: set[tuple[str, str]] = set()
        completed_tasks: set[tuple[str, str, str]] = set()
        course_terminal: set[str] = set()

        def course_key(course: Mapping[str, Any]) -> str:
            return str(self._course_id(course) or "")

        def chapter_key(course: Mapping[str, Any], point: Mapping[str, Any]) -> tuple[str, str]:
            return course_key(course), self._chapter_id(point)

        def job_key(
            course: Mapping[str, Any], point: Mapping[str, Any], job: Mapping[str, Any]
        ) -> tuple[str, str, str]:
            return (*chapter_key(course, point), self._job_id(job))

        def report_tree() -> None:
            with tree_lock:
                snapshot = copy.deepcopy(course_nodes)
            self._report(context.reporter, "set_courses", snapshot)

        def report_counts() -> None:
            with tree_lock, counts_lock:
                counts["completed_courses"] = len(completed_courses)
                counts["completed_chapters"] = len(completed_chapters)
                counts["completed_tasks"] = len(completed_tasks)
                counts["total_chapters"] = sum(
                    len(course.get("chapters", [])) for course in course_nodes
                )
                counts["total_tasks"] = sum(
                    len(chapter.get("jobs", []))
                    for course in course_nodes
                    for chapter in course.get("chapters", [])
                )
                snapshot = dict(counts)
            self._report(context.reporter, "set_counts", **snapshot)

        def report_active_jobs() -> None:
            with tree_lock:
                snapshot = copy.deepcopy(active_jobs)
            self._report(context.reporter, "set_active_jobs", snapshot)

        def ensure_course_node(course: Mapping[str, Any]) -> dict[str, Any]:
            key = course_key(course)
            node = course_by_id.get(key)
            if node is None:
                node = {
                    "id": self._course_id(course),
                    "title": self._course_title(course),
                    "status": "pending",
                    "chapters": [],
                }
                course_by_id[key] = node
                course_nodes.append(node)
            return node

        def ensure_chapter_node(
            course: Mapping[str, Any], point: Mapping[str, Any]
        ) -> dict[str, Any]:
            key = chapter_key(course, point)
            node = chapter_by_key.get(key)
            if node is None:
                node = {
                    "id": self._chapter_id(point),
                    "title": self._chapter_title(point),
                    "status": "completed" if point.get("has_finished") else "pending",
                    "jobs": [],
                }
                chapter_by_key[key] = node
                ensure_course_node(course).setdefault("chapters", []).append(node)
            return node

        def course_points_callback(
            course: Mapping[str, Any], points: Any
        ) -> None:
            if context.cancel_event.is_set():
                raise StudyCancelled()
            if isinstance(points, Mapping):
                points = points.get("points", [])
            if not isinstance(points, (list, tuple)):
                points = []
            with tree_lock:
                ensure_course_node(course)
                for point in points:
                    if isinstance(point, Mapping):
                        ensure_chapter_node(course, point)
            report_counts()
            report_tree()

        def course_start_callback(course: Mapping[str, Any]) -> None:
            if context.cancel_event.is_set():
                raise StudyCancelled()
            with tree_lock:
                node = ensure_course_node(course)
                node["status"] = "running"
            self._report(
                context.reporter,
                "set_current",
                course=self._course_title(course),
                chapter=None,
            )
            report_tree()

        def course_done_callback(course: Mapping[str, Any]) -> None:
            if context.cancel_event.is_set():
                raise StudyCancelled()
            key = course_key(course)
            with tree_lock:
                node = ensure_course_node(course)
                node["status"] = "completed"
                completed_courses.add(key)
                course_terminal.add(key)
            report_counts()
            report_tree()

        def course_failed_callback(course: Mapping[str, Any]) -> None:
            if context.cancel_event.is_set():
                raise StudyCancelled()
            key = course_key(course)
            with tree_lock:
                node = ensure_course_node(course)
                node["status"] = "failed"
                course_terminal.add(key)
            report_tree()

        def chapter_status_callback(
            course: Mapping[str, Any], point: Mapping[str, Any], status: str
        ) -> None:
            if context.cancel_event.is_set():
                raise StudyCancelled()
            key = chapter_key(course, point)
            normalized_status = str(status or "failed").strip().lower()
            if normalized_status not in {"failed", "not_open", "running"}:
                normalized_status = "failed"
            with tree_lock:
                chapter = ensure_chapter_node(course, point)
                chapter["status"] = normalized_status
                if normalized_status != "completed":
                    completed_chapters.discard(key)
            report_tree()

        def chapter_start_callback(course: Mapping[str, Any], point: Mapping[str, Any]):
            if context.cancel_event.is_set():
                raise StudyCancelled()
            with tree_lock:
                node = ensure_chapter_node(course, point)
                node["status"] = "running"
            self._report(
                context.reporter,
                "set_current",
                course=self._course_title(course),
                chapter=self._chapter_title(point),
            )
            report_tree()

        def job_list_callback(
            course: Mapping[str, Any],
            point: Mapping[str, Any],
            jobs: Any,
            job_info: Mapping[str, Any] | None = None,
        ) -> None:
            if context.cancel_event.is_set():
                raise StudyCancelled()
            if not isinstance(jobs, (list, tuple)):
                jobs = []
            with tree_lock:
                chapter = ensure_chapter_node(course, point)
                if isinstance(job_info, Mapping) and job_info.get("notOpen"):
                    chapter["status"] = "not_open"
                current_by_id = {
                    str(job.get("id", job.get("jobid", job.get("jobId", "")))): job
                    for job in chapter.get("jobs", [])
                    if isinstance(job, Mapping)
                }
                next_jobs: list[dict[str, Any]] = []
                for job in jobs:
                    if not isinstance(job, Mapping):
                        continue
                    key = job_key(course, point, job)
                    existing = job_by_key.get(key)
                    if existing is None:
                        existing = {
                            "id": self._job_id(job),
                            "title": self._job_title(job),
                            "status": "pending",
                        }
                        job_by_key[key] = existing
                    elif existing.get("id") == "" and current_by_id.get("") is not None:
                        existing["title"] = self._job_title(job)
                    next_jobs.append(existing)
                chapter["jobs"] = next_jobs
            report_counts()
            report_tree()

        def job_start_callback(
            course: Mapping[str, Any], point: Mapping[str, Any], job: Mapping[str, Any]
        ) -> None:
            if context.cancel_event.is_set():
                raise StudyCancelled()
            key = job_key(course, point, job)
            with tree_lock:
                chapter = ensure_chapter_node(course, point)
                item = job_by_key.get(key)
                if item is None:
                    item = {
                        "id": self._job_id(job),
                        "title": self._job_title(job),
                        "status": "pending",
                    }
                    job_by_key[key] = item
                    chapter.setdefault("jobs", []).append(item)
                item["status"] = "running"
                active_jobs[":".join(key)] = {
                    "id": item["id"],
                    "title": item["title"],
                    "course": self._course_title(course),
                    "chapter": self._chapter_title(point),
                    "status": "running",
                }
            self._report(
                context.reporter,
                "set_current",
                course=self._course_title(course),
                chapter=self._chapter_title(point),
                task=self._job_title(job),
            )
            report_active_jobs()
            report_tree()

        def job_done_callback(
            course: Mapping[str, Any],
            point: Mapping[str, Any],
            job: Mapping[str, Any],
            result: Any,
        ) -> None:
            if context.cancel_event.is_set():
                raise StudyCancelled()
            key = job_key(course, point, job)
            with tree_lock:
                item = job_by_key.get(key)
                if item is None:
                    item = {
                        "id": self._job_id(job),
                        "title": self._job_title(job),
                        "status": "pending",
                    }
                    job_by_key[key] = item
                    ensure_chapter_node(course, point).setdefault("jobs", []).append(item)
                status = self._job_result_status(result)
                item["status"] = status
                active_jobs.pop(":".join(key), None)
                if status == "completed":
                    completed_tasks.add(key)
            report_active_jobs()
            report_counts()
            report_tree()

        def chapter_done_callback(course: Mapping[str, Any], point: Mapping[str, Any]):
            if context.cancel_event.is_set():
                raise StudyCancelled()
            key = chapter_key(course, point)
            with tree_lock:
                chapter = ensure_chapter_node(course, point)
                chapter["status"] = "completed"
                completed_chapters.add(key)
            report_counts()
            report_tree()

        def video_progress_callback(
            course: Mapping[str, Any],
            job: Mapping[str, Any],
            progress: float,
            total: float,
        ):
            if context.cancel_event.is_set():
                raise StudyCancelled()
            # Keep the chapter callback as the authoritative current label;
            # video updates only add progress metadata.  This also avoids
            # making a fast stream of progress callbacks reorder the UI's
            # current-course/current-chapter display.  ``process_job`` does
            # not carry the chapter argument, so locate the active item by
            # its account-local course and job identity.
            job_id = self._job_id(job)
            job_title = self._job_title(job)
            with tree_lock:
                for active in active_jobs.values():
                    if (
                        active.get("course") == self._course_title(course)
                        and (
                            (job_id and active.get("id") == job_id)
                            or (not job_id and active.get("title") == job_title)
                        )
                    ):
                        active["progress"] = progress
                        active["total"] = total
            self._report(
                context.reporter,
                "set_counts",
                video_progress=progress,
                video_total=total,
            )
            report_active_jobs()

        common_config.update(
            {
                "course_points_callback": course_points_callback,
                "course_start_callback": course_start_callback,
                "course_done_callback": course_done_callback,
                "course_failed_callback": course_failed_callback,
                "chapter_start_callback": chapter_start_callback,
                "chapter_status_callback": chapter_status_callback,
                "chapter_done_callback": chapter_done_callback,
                "job_list_callback": job_list_callback,
                "job_start_callback": job_start_callback,
                "job_done_callback": job_done_callback,
                "video_progress_callback": video_progress_callback,
            }
        )
        callbacks = {
            "course_points_callback": course_points_callback,
            "course_start_callback": course_start_callback,
            "course_done_callback": course_done_callback,
            "course_failed_callback": course_failed_callback,
            "chapter_start_callback": chapter_start_callback,
            "chapter_status_callback": chapter_status_callback,
            "chapter_done_callback": chapter_done_callback,
            "job_list_callback": job_list_callback,
            "job_start_callback": job_start_callback,
            "job_done_callback": job_done_callback,
            "video_progress_callback": video_progress_callback,
        }

        try:
            session, cookie_update_callback = self._cookie_callback(context)
            common_config["cookie_path"] = str(
                account_cookie_path(context.account_id, self.data_dir / "cookies.txt")
            )
            request = EngineFactoryRequest(
                context=context,
                auth=context.auth,
                course_ids=list(context.course_ids),
                common_config=common_config,
                tiku_config=tiku_config,
                answer_semaphore=context.answer_semaphore,
                session=session,
                cookie_update_callback=cookie_update_callback,
            )
        except StudyCancelled:
            raise
        except BaseException as exc:
            raise StudyRunError(self._safe_error(context, exc)) from None

        try:
            with vision_ocr_context(preferences.ocr_config):
                if context.cancel_event.is_set():
                    raise StudyCancelled()
                engine = _invoke_factory(
                    self.engine_factory,
                    {
                        "context": context,
                        "auth": context.auth,
                        "course_ids": list(context.course_ids),
                        "common_config": common_config,
                        "config": common_config,
                        "tiku_config": tiku_config,
                        "callbacks": callbacks,
                        "course_points_callback": course_points_callback,
                        "course_start_callback": course_start_callback,
                        "course_done_callback": course_done_callback,
                        "course_failed_callback": course_failed_callback,
                        "chapter_start_callback": chapter_start_callback,
                        "chapter_status_callback": chapter_status_callback,
                        "chapter_done_callback": chapter_done_callback,
                        "job_list_callback": job_list_callback,
                        "job_start_callback": job_start_callback,
                        "job_done_callback": job_done_callback,
                        "video_progress_callback": video_progress_callback,
                        "answer_semaphore": context.answer_semaphore,
                        "session": session,
                        "cookie_update_callback": cookie_update_callback,
                    },
                    request,
                )
                if context.cancel_event.is_set():
                    raise StudyCancelled()

                login_result = _invoke_login(
                    engine,
                    login_with_cookies=use_cookies,
                )
                if not _result_status(login_result):
                    message = (
                        login_result.get("msg", "study login failed")
                        if isinstance(login_result, Mapping)
                        else "study login failed"
                    )
                    raise StudyRunError(self._safe_error(context, message))
                if context.cancel_event.is_set():
                    raise StudyCancelled()

                get_courses = getattr(engine, "get_course_list", None)
                if not callable(get_courses):
                    raise StudyRunError("study engine does not provide course retrieval")
                all_courses = self._course_items(get_courses())

                # Keep filtering and course processing in the existing engine.
                import main

                requested_course_ids = [str(item) for item in context.course_ids]
                available_course_ids = {
                    str(self._course_id(course))
                    for course in all_courses
                    if isinstance(course, Mapping) and self._course_id(course) is not None
                }
                missing_course_ids = [
                    course_id
                    for course_id in requested_course_ids
                    if course_id not in available_course_ids
                ]
                if requested_course_ids and missing_course_ids:
                    # Web task IDs are explicit and must never silently fall
                    # back to “all courses”.  The CLI keeps main.filter_courses
                    # unchanged for its interactive/legacy path.
                    raise CourseSelectionInvalid()
                course_task = main.filter_courses(
                    list(all_courses), requested_course_ids
                )
                selected_courses = list(course_task or [])
                selected_course_ids = {
                    str(self._course_id(course))
                    for course in selected_courses
                    if isinstance(course, Mapping) and self._course_id(course) is not None
                }
                if requested_course_ids and selected_course_ids != set(requested_course_ids):
                    raise CourseSelectionInvalid()
                with counts_lock:
                    counts["total_courses"] = len(selected_courses)
                    initial_counts = dict(counts)
                self._report(context.reporter, "set_counts", **initial_counts)
                for course in selected_courses:
                    ensure_course_node(course)
                report_tree()

                for course in selected_courses:
                    if context.cancel_event.is_set():
                        raise StudyCancelled()
                    course_start_callback(course)
                    process_course = getattr(engine, "process_course", None)
                    try:
                        if callable(process_course):
                            # A test/integration engine may expose the existing
                            # course processor as a method.  The real Chaoxing
                            # client does not, so production continues through
                            # ``main.process_course`` below.
                            signature = _callable_signature(process_course)
                            if signature is not None and len(signature.parameters) <= 1:
                                process_course(course)
                            else:
                                process_course(course, common_config)
                        else:
                            main.process_course(engine, course, common_config)
                    except StudyCancelled:
                        raise
                    except BaseException:
                        course_failed_callback(course)
                        raise
                    if context.cancel_event.is_set():
                        raise StudyCancelled()
                    # The legacy processor emits either course_done or
                    # course_failed after it has observed all chapter results.
                    # Keep a safe fallback for injected engines that expose a
                    # processor but do not emit a terminal callback.
                    if course_key(course) not in course_terminal:
                        course_done_callback(course)
        except StudyCancelled:
            raise
        except StudyRunError as exc:
            # An injected engine may raise this type itself; keep the same
            # public error class while still applying the task's secret
            # redaction boundary.
            raise StudyRunError(self._safe_error(context, exc), code=exc.code) from None
        except BaseException as exc:
            raise StudyRunError(self._safe_error(context, exc)) from None


__all__ = [
    "ChaoxingStudyRunner",
    "EngineFactoryRequest",
    "StudyCancelled",
    "CourseSelectionInvalid",
    "StudyRunError",
]
