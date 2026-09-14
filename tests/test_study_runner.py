"""Focused tests for the task-scoped study runner adapter."""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from api.vision_ocr import _load_vision_ocr_config, vision_ocr_context
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


def test_runner_propagates_account_and_answer_context(
    tmp_path, fake_context, fake_engine_factory
):
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    runner.run(fake_context)
    call = fake_engine_factory.last_call
    assert call.auth.cookies == {"_uid": "account-a"}
    assert call.course_ids == ["math"]
    assert call.answer_semaphore is fake_context.answer_semaphore
    assert call.tiku_config["submit"] == "false"
    assert call.tiku_config["cache_file"].endswith("answer-cache.json")


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
