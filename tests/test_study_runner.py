"""Focused tests for the task-scoped study runner adapter."""

from __future__ import annotations

import os
import io
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

import api.decode as decode
import api.answer as answer
import api.base as base
from api.answer import AI
from api.base import Account, Chaoxing, StudyResult
from api.vision_ocr import _load_vision_ocr_config, vision_ocr_context
from api.vision_ocr import reset_vision_ocr_config
from api.logger import logger
from webapp.models import AccountAuth, AccountPreferences, ResolvedAnswerConnection
from webapp.study_runner import ChaoxingStudyRunner, StudyCancelled, StudyRunError
from webapp.task_manager import StudyRunContext


class FakeReporter:
    def __init__(self):
        self.current_updates = []
        self.count_updates = []
        self.course_updates = []

    def set_current(self, **values):
        self.current_updates.append(dict(values))

    def set_counts(self, **values):
        self.count_updates.append(dict(values))

    def set_courses(self, courses):
        self.course_updates.append(courses)


class FakeEngine:
    def __init__(self, common_config):
        self.common_config = common_config
        self.rate_limiter = SimpleNamespace(limit_rate=lambda **_: None)
        self.session = SimpleNamespace(cookies=SimpleNamespace(get=lambda *_: "uid"))

    def login(self, login_with_cookies=False):
        return {"status": True}

    def get_course_list(self):
        return [
            {
                "courseId": "math",
                "title": "高等数学",
                "clazzId": "clazz",
                "cpi": "cpi",
            }
        ]

    def get_course_point(self, *_args):
        return {
            "points": [
                {
                    "id": "chapter-1",
                    "title": "第一章",
                    "has_finished": False,
                    "jobCount": 1,
                }
            ]
        }

    def get_job_list(self, *_args):
        return [], {"notOpen": False}


class FakeEngineFactory:
    def __init__(self):
        self.last_call = None
        self._failure = None
        self.processed_courses = []

    def raise_with_message(self, message):
        self._failure = message

    def __call__(self, **kwargs):
        self.last_call = SimpleNamespace(**kwargs)
        if self._failure is not None:
            raise RuntimeError(self._failure)
        return FakeEngine(kwargs["common_config"])


@pytest.fixture
def fake_context():
    preferences = AccountPreferences(
        selected_course_ids=["math"],
        speed=1.0,
        jobs=1,
        answer_enabled=True,
        answer_cover_rate=0.9,
        answer_auto_submit=False,
        ocr_config={"provider": "openai"},
    )
    answer = ResolvedAnswerConnection(
        enabled=True,
        base_url="http://localhost:8849/v1",
        model="test-model",
        api_key="bearer-a",
    )
    reporter = FakeReporter()
    return StudyRunContext(
        task_id="task-a",
        account_id="account-a",
        course_ids=["math"],
        preferences=preferences,
        auth=AccountAuth(
            username="user-a",
            password="password-a",
            cookies={"_uid": "account-a"},
        ),
        answer=answer,
        answer_semaphore=threading.Semaphore(1),
        cancel_event=threading.Event(),
        reporter=reporter,
    )


@pytest.fixture
def fake_engine_factory():
    return FakeEngineFactory()


def test_runner_reports_courses_and_current_chapter(
    tmp_path, fake_context, fake_engine_factory
):
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    runner.run(fake_context)
    assert fake_context.reporter.current_updates[-1] == {
        "course": "高等数学",
        "chapter": "第一章",
        "task": None,
    }
    assert fake_context.reporter.count_updates[-1]["completed_courses"] == 1


def test_runner_stops_at_next_safe_checkpoint(
    tmp_path, fake_context, fake_engine_factory
):
    fake_context.cancel_event.set()
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    with pytest.raises(StudyCancelled):
        runner.run(fake_context)
    assert fake_engine_factory.processed_courses == []


def test_refresh_video_status_handles_non_200_without_secondary_error():
    engine = object.__new__(Chaoxing)
    engine.rate_limiter = SimpleNamespace(limit_rate=lambda **_: None)
    engine.get_fid = lambda: "fid"
    response = SimpleNamespace(status_code=403, text="sensitive-response")
    session = SimpleNamespace(get=lambda *_args, **_kwargs: response)
    visible_logs = io.StringIO()
    sink_id = logger.add(visible_logs, format="{message}", level="TRACE")
    try:
        assert engine._refresh_video_status(
            session, {"objectid": "object"}, "Video"
        ) is None
    finally:
        logger.remove(sink_id)
    assert "403" in visible_logs.getvalue()
    assert "sensitive-response" not in visible_logs.getvalue()


def test_runner_propagates_account_and_answer_context(
    tmp_path, fake_context, fake_engine_factory
):
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    runner.run(fake_context)
    call = fake_engine_factory.last_call
    assert call.auth.cookies == {"_uid": "account-a"}
    assert call.course_ids == ["math"]
    assert call.answer_semaphore is fake_context.answer_semaphore
    assert call.common_config["_log_secrets"] == (
        "password-a",
        "account-a",
        "bearer-a",
    )
    assert call.tiku_config["submit"] == "false"
    assert call.tiku_config["cache_file"].endswith("answer-cache.json")


@pytest.mark.parametrize(
    ("base_url", "expected_endpoint"),
    [
        (
            "http://localhost:8849/v1",
            "http://host.docker.internal:8849/v1/chat/completions",
        ),
        (
            "http://127.0.0.1:8849/v1",
            "http://host.docker.internal:8849/v1/chat/completions",
        ),
        (
            "http://192.168.1.8:8849/v1",
            "http://192.168.1.8:8849/v1/chat/completions",
        ),
    ],
)
def test_runner_builds_docker_aware_ai_endpoint_without_rewriting_saved_url(
    tmp_path, fake_context, fake_engine_factory, base_url, expected_endpoint
):
    context = replace(
        fake_context,
        answer=replace(fake_context.answer, base_url=base_url),
    )
    runner = ChaoxingStudyRunner(
        data_dir=tmp_path,
        engine_factory=fake_engine_factory,
        running_in_docker=True,
    )

    runner.run(context)

    assert fake_engine_factory.last_call.tiku_config["endpoint"] == expected_endpoint
    assert context.answer.base_url == base_url


def test_runner_sanitizes_secret_values_from_failure(
    tmp_path, fake_context, fake_engine_factory
):
    fake_context = replace(
        fake_context,
        auth=replace(fake_context.auth, cookies={"sid": "cookie-a"}),
    )
    fake_engine_factory.raise_with_message("password-a cookie-a bearer-a")
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    with pytest.raises(StudyRunError) as captured:
        runner.run(fake_context)
    rendered = str(captured.value)
    assert "password-a" not in rendered
    assert "cookie-a" not in rendered
    assert "bearer-a" not in rendered


def test_runner_sanitizes_ocr_secret_values_from_factory_failure(
    tmp_path, fake_context, fake_engine_factory
):
    fake_context = replace(
        fake_context,
        preferences=replace(
            fake_context.preferences,
            ocr_config={"provider": "openai", "key": "ocr-secret"},
        ),
    )
    fake_engine_factory.raise_with_message("worker failed with ocr-secret")
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    with pytest.raises(StudyRunError) as captured:
        runner.run(fake_context)
    assert "ocr-secret" not in str(captured.value)


class _OCRSession:
    def __init__(self, cookie):
        self.cookie = cookie
        self.headers = {}
        self.seen = []

    def get(self, url, headers=None, timeout=None):
        self.seen.append((url, dict(headers or {}), timeout, self.cookie))
        return SimpleNamespace(status_code=200, content=b"image")


def test_ocr_download_uses_only_each_account_session(monkeypatch):
    first = _OCRSession("cookie-account-a")
    second = _OCRSession("cookie-account-b")
    monkeypatch.setattr(decode, "ENABLE_LOCAL_OCR", False)
    monkeypatch.setattr(decode, "vision_ocr", lambda image: "recognized")

    def unexpected_global_session():
        raise AssertionError("OCR created a global/default session")

    monkeypatch.setattr(decode.requests, "Session", unexpected_global_session)
    monkeypatch.setattr(decode, "use_cookies", unexpected_global_session)
    with vision_ocr_context(
        {"provider": "openai", "api_key": "ocr-key", "endpoint": "http://ocr"}
    ):
        assert decode._ocr_image_to_text("https://image-a", session=first) == "recognized"
        assert decode._ocr_image_to_text("https://image-b", session=second) == "recognized"

    assert [item[3] for item in first.seen] == ["cookie-account-a"]
    assert [item[3] for item in second.seen] == ["cookie-account-b"]


class _OCRContextRunner:
    def run_two(self, first, second):
        values = [None, None]

        def run(index, config):
            with vision_ocr_context(config):
                values[index] = _load_vision_ocr_config()["provider"]

        threads = [
            threading.Thread(target=run, args=(0, first)),
            threading.Thread(target=run, args=(1, second)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return values


@pytest.fixture
def ocr_context_runner():
    return _OCRContextRunner()


def test_parallel_ocr_contexts_do_not_touch_process_environment(ocr_context_runner):
    before = dict(os.environ)
    results = ocr_context_runner.run_two({"provider": "openai"}, {"provider": "claude"})
    assert results == ["openai", "claude"]
    assert dict(os.environ) == before


def test_empty_task_ocr_context_masks_environment(monkeypatch):
    monkeypatch.setenv("CHAOXING_VISION_OCR_PROVIDER", "openai")
    monkeypatch.setenv("CHAOXING_VISION_OCR_KEY", "environment-secret")
    reset_vision_ocr_config()
    try:
        with vision_ocr_context({}):
            assert _load_vision_ocr_config() is None
    finally:
        reset_vision_ocr_config()


class _MemoryAnswerCache:
    def __init__(self):
        self.values = {}

    def get_cache(self, question):
        return self.values.get(question)

    def add_cache(self, question, value):
        self.values[question] = value


class _NestedAnswerAI(AI):
    def __init__(self):
        super().__init__()
        self._cache = _MemoryAnswerCache()
        self.titles = []

    def _query(self, q_info):
        self.titles.append(q_info["title"])
        return "A"


class _AnswerSession:
    def __init__(self, cookie):
        self.cookie = cookie
        self.headers = {}
        self.image_cookies = []
        self.posted_urls = []

    def get(self, url, **_kwargs):
        if "mooc-ans/api/work" in url:
            return SimpleNamespace(status_code=200, text="work html")
        if "p.ananas.chaoxing.com" in url:
            self.image_cookies.append(self.cookie)
            return SimpleNamespace(status_code=200, content=b"image")
        raise AssertionError(f"unexpected account request: {url}")

    def post(self, _url, **_kwargs):
        self.posted_urls.append(_url)
        return SimpleNamespace(
            status_code=200,
            text="ok",
            json=lambda: {"status": True, "msg": "saved"},
        )


def _nested_questions():
    return {
        "questions": [
            {
                "id": "q1",
                "title": '<img src="https://p.ananas.chaoxing.com/formula-a.png">',
                "options": "A. first\nB. second",
                "type": "single",
                "answerField": {"answerq1": "", "answertypeq1": "0"},
            },
            {
                "id": "q2",
                "title": '<img src="https://p.ananas.chaoxing.com/formula-b.png">',
                "options": "A. first\nB. second",
                "type": "single",
                "answerField": {"answerq2": "", "answertypeq2": "0"},
            },
        ]
    }


def _run_nested_answer(session, ocr_config):
    tiku = _NestedAnswerAI()
    chaoxing = Chaoxing(
        Account("answer-user", "answer-password"),
        tiku=tiku,
        session=session,
        task_id="nested-answer-task",
        ocr_config=ocr_config,
        ai_concurrency=2,
    )
    with vision_ocr_context(ocr_config):
        result = chaoxing.study_work(
            {"courseId": "course", "clazzId": "clazz"},
            {"jobid": "work-1", "enc": "enc"},
            {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
        )
    return result, tiku


def test_nested_ai_answer_ocr_uses_each_account_session(monkeypatch):
    monkeypatch.setattr(base, "decode_questions_info", lambda *_args, **_kwargs: _nested_questions())
    monkeypatch.setattr(decode, "requests", SimpleNamespace(Session=lambda: (_ for _ in ()).throw(
        AssertionError("nested OCR created a default session")
    )))
    monkeypatch.setattr(decode, "is_vision_ocr_enabled", lambda: True)
    monkeypatch.setattr(answer, "is_vision_ocr_enabled", lambda: True)
    monkeypatch.setattr(decode, "vision_ocr", lambda _image: "formula")

    first = _AnswerSession("cookie-account-a")
    second = _AnswerSession("cookie-account-b")
    first_result, _ = _run_nested_answer(
        first, {"provider": "openai", "api_key": "task-a-key", "endpoint": "http://ocr"}
    )
    second_result, _ = _run_nested_answer(
        second, {"provider": "openai", "api_key": "task-b-key", "endpoint": "http://ocr"}
    )

    assert first_result is StudyResult.SKIPPED
    assert second_result is StudyResult.SKIPPED
    assert first.image_cookies == ["cookie-account-a", "cookie-account-a"]
    assert second.image_cookies == ["cookie-account-b", "cookie-account-b"]


def test_nested_ai_answer_workers_reenter_empty_ocr_context(monkeypatch):
    monkeypatch.setenv("CHAOXING_VISION_OCR_PROVIDER", "openai")
    monkeypatch.setenv("CHAOXING_VISION_OCR_KEY", "environment-secret")
    reset_vision_ocr_config()
    monkeypatch.setattr(base, "decode_questions_info", lambda *_args, **_kwargs: _nested_questions())
    ocr_calls = []

    def fake_ocr(src, **_kwargs):
        ocr_calls.append(src)
        return "environment-ocr"

    monkeypatch.setattr(answer, "_ocr_image_to_text", fake_ocr)
    session = _AnswerSession("cookie-account-empty")
    try:
        result, tiku = _run_nested_answer(session, {})
    finally:
        reset_vision_ocr_config()

    assert result is StudyResult.SKIPPED
    assert ocr_calls == []
    assert all("environment-ocr" not in title for title in tiku.titles)


@pytest.mark.parametrize("failure", [StudyCancelled, RuntimeError])
def test_ai_study_work_propagates_worker_future_failures(monkeypatch, failure):
    monkeypatch.setattr(
        base,
        "decode_questions_info",
        lambda *_args, **_kwargs: _nested_questions(),
    )

    class FailingAI(AI):
        def query(self, _question):
            raise failure("worker failure")

    tiku = FailingAI()
    session = _AnswerSession("cookie-account-failure")
    chaoxing = Chaoxing(
        Account("answer-user", "answer-password"),
        tiku=tiku,
        session=session,
        ai_concurrency=2,
    )

    with pytest.raises(failure):
        chaoxing.study_work(
            {"courseId": "course", "clazzId": "clazz"},
            {"jobid": "work-1", "enc": "enc"},
            {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
        )
    assert session.posted_urls == []


def test_ai_study_work_cancellation_is_checked_before_coverage(monkeypatch):
    monkeypatch.setattr(
        base,
        "decode_questions_info",
        lambda *_args, **_kwargs: _nested_questions(),
    )
    cancel_event = threading.Event()

    class CancellingAI(AI):
        def query(self, _question):
            cancel_event.set()
            return "A"

    tiku = CancellingAI()
    session = _AnswerSession("cookie-account-cancel")
    chaoxing = Chaoxing(
        Account("answer-user", "answer-password"),
        tiku=tiku,
        session=session,
        ai_concurrency=2,
        cancel_event=cancel_event,
    )

    with pytest.raises(StudyCancelled):
        chaoxing.study_work(
            {"courseId": "course", "clazzId": "clazz"},
            {"jobid": "work-1", "enc": "enc"},
            {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
        )
    assert session.posted_urls == []


def test_ai_study_work_rechecks_cancellation_before_submit(monkeypatch):
    monkeypatch.setattr(
        base,
        "decode_questions_info",
        lambda *_args, **_kwargs: _nested_questions(),
    )
    cancel_event = threading.Event()

    class CancellingAI(AI):
        def query(self, _question):
            return "A"

        def get_submit_params(self):
            cancel_event.set()
            return "1"

    tiku = CancellingAI()
    session = _AnswerSession("cookie-account-cancel-before-submit")
    chaoxing = Chaoxing(
        Account("answer-user", "answer-password"),
        tiku=tiku,
        session=session,
        ai_concurrency=2,
        cancel_event=cancel_event,
    )

    with pytest.raises(StudyCancelled):
        chaoxing.study_work(
            {"courseId": "course", "clazzId": "clazz"},
            {"jobid": "work-1", "enc": "enc"},
            {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
        )
    assert session.posted_urls == []


def test_blocked_live_reason_reaches_task_details_without_retry(tmp_path, fake_context, monkeypatch):
    import main
    from api.live_process import LiveUnavailable
    engine = FakeEngine({})
    calls = []
    engine.get_uid = lambda: 'uid'
    engine.get_job_list = lambda *_: ([{'type': 'live', 'jobid': 'upcoming', 'title': '第一次直播'}], {'knowledgeid': 'chapter-1'})
    def unavailable(*_, **__):
        calls.append(1)
        raise LiveUnavailable('直播尚未开始', '等待老师开启直播后再运行。')
    monkeypatch.setattr(main.LiveProcessor, 'run_live', unavailable)
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=lambda **_: engine)
    with pytest.raises(StudyRunError):
        runner.run(fake_context)
    course = fake_context.reporter.course_updates[-1][0]
    chapter = course['chapters'][0]
    job = chapter['jobs'][0]
    assert course['status'] == 'failed'
    assert chapter['status'] == job['status'] == 'blocked'
    assert job['reason'] == '直播尚未开始'
    assert job['next_action'] == '等待老师开启直播后再运行。'
    assert len(calls) == 1
    assert fake_context.reporter.count_updates[-1]['completed_tasks'] == 0
