"""Task-scoped adapter for the existing Chaoxing learning engine.

The legacy command-line program keeps its learning algorithm in :mod:`main`.
This module supplies the account/task values owned by ``TaskManager`` and
translates the engine's callbacks into the task reporter API.  It deliberately
does not implement a second course/chapter/job processor.
"""

from __future__ import annotations

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

from .answer_connection import normalize_completion_url
from .task_manager import StudyRunContext, _secret_values as _context_secret_values


class StudyRunError(RuntimeError):
    """Raised when account-specific study setup or processing fails."""


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
    ) -> None:
        self.data_dir = Path(data_dir)
        self.engine_factory = engine_factory or _default_engine_factory
        self.cookie_update_callback_factory = cookie_update_callback_factory

    @staticmethod
    def _secret_values(context: StudyRunContext) -> tuple[str, ...]:
        return _context_secret_values(
            context.auth,
            context.answer,
            context.preferences.ocr_config,
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

    @staticmethod
    def _answer_config(context: StudyRunContext) -> dict[str, Any]:
        preferences = context.preferences
        answer = context.answer
        base_url = ""
        if answer is not None:
            base_value = getattr(answer, "outbound_base_url", None)
            if base_value is None:
                base_value = getattr(answer, "base_url", "")
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

    @staticmethod
    def _course_title(course: Mapping[str, Any]) -> str:
        return str(course.get("title", course.get("name", "")) or "")

    @staticmethod
    def _chapter_title(point: Mapping[str, Any]) -> str:
        return str(point.get("title", point.get("name", "")) or "")

    def run(self, context: StudyRunContext) -> None:
        """Run one account context and report progress to its reporter."""

        if context.cancel_event.is_set():
            raise StudyCancelled()

        preferences = context.preferences
        tiku_config = self._answer_config(context)
        tiku_config["cache_file"] = str(self.data_dir / "answer-cache.json")

        common_config: dict[str, Any] = {
            "use_cookies": bool(context.auth.cookies),
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

        def chapter_start_callback(course: Mapping[str, Any], point: Mapping[str, Any]):
            if context.cancel_event.is_set():
                raise StudyCancelled()
            self._report(
                context.reporter,
                "set_current",
                course=self._course_title(course),
                chapter=self._chapter_title(point),
            )

        def chapter_done_callback(course: Mapping[str, Any], point: Mapping[str, Any]):
            if context.cancel_event.is_set():
                raise StudyCancelled()
            try:
                job_count = int(point.get("jobCount", 1) or 1)
            except (TypeError, ValueError):
                job_count = 1
            with counts_lock:
                counts["completed_chapters"] += 1
                counts["completed_tasks"] += job_count
                snapshot = dict(counts)
            self._report(context.reporter, "set_counts", **snapshot)

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
            # current-course/current-chapter display.
            self._report(
                context.reporter,
                "set_counts",
                video_progress=progress,
                video_total=total,
            )

        common_config.update(
            {
                "chapter_start_callback": chapter_start_callback,
                "chapter_done_callback": chapter_done_callback,
                "video_progress_callback": video_progress_callback,
            }
        )
        callbacks = {
            "chapter_start_callback": chapter_start_callback,
            "chapter_done_callback": chapter_done_callback,
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
                        "chapter_start_callback": chapter_start_callback,
                        "chapter_done_callback": chapter_done_callback,
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
                    engine, login_with_cookies=bool(context.auth.cookies)
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
                all_courses = get_courses() or []

                # Keep filtering and course processing in the existing engine.
                import main

                course_task = main.filter_courses(
                    list(all_courses), list(context.course_ids)
                )
                selected_courses = list(course_task or [])
                with counts_lock:
                    counts["total_courses"] = len(selected_courses)
                    initial_counts = dict(counts)
                self._report(context.reporter, "set_counts", **initial_counts)
                self._report(
                    context.reporter,
                    "set_courses",
                    [
                        {
                            "id": self._course_id(course),
                            "title": self._course_title(course),
                            "status": "pending",
                        }
                        for course in selected_courses
                    ],
                )

                for course in selected_courses:
                    if context.cancel_event.is_set():
                        raise StudyCancelled()
                    self._report(
                        context.reporter,
                        "set_current",
                        course=self._course_title(course),
                        chapter=None,
                    )
                    process_course = getattr(engine, "process_course", None)
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
                    if context.cancel_event.is_set():
                        raise StudyCancelled()
                    with counts_lock:
                        counts["completed_courses"] += 1
                        final_counts = dict(counts)
                    self._report(context.reporter, "set_counts", **final_counts)
        except StudyCancelled:
            raise
        except StudyRunError as exc:
            # An injected engine may raise this type itself; keep the same
            # public error class while still applying the task's secret
            # redaction boundary.
            raise StudyRunError(self._safe_error(context, exc)) from None
        except BaseException as exc:
            raise StudyRunError(self._safe_error(context, exc)) from None


__all__ = [
    "ChaoxingStudyRunner",
    "EngineFactoryRequest",
    "StudyCancelled",
    "StudyRunError",
]
