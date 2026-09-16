"""Round-seven regressions for deterministic live-job failure cancellation."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

import main
from api.base import StudyResult


def _processor_config(**values):
    config = {
        "speed": 1.0,
        "jobs": 1,
        "notopen_action": "continue",
        "cancel_event": threading.Event(),
    }
    config.update(values)
    return config


class _Engine:
    rate_limiter = SimpleNamespace(limit_rate=lambda **_kwargs: None)

    def get_job_list(self, _course, _point):
        return (
            [
                {"type": "video", "jobid": "predecessor"},
                {"type": "document", "jobid": "later-failure"},
            ],
            {},
        )


def _processor(**config_values):
    return main.JobProcessor(
        _Engine(),
        {"courseId": "course", "title": "course"},
        [main.ChapterTask(index=0, point={"id": "chapter"})],
        _processor_config(**config_values),
    )


def test_later_job_failure_interrupts_owner_waiting_on_noncooperative_predecessor(
    monkeypatch,
):
    predecessor_started = threading.Event()
    release_predecessor = threading.Event()

    def process_job(_chaoxing, _course, job, _job_info, _speed, **_kwargs):
        if job["jobid"] == "predecessor":
            predecessor_started.set()
            assert release_predecessor.wait(timeout=3)
            return StudyResult.SUCCESS
        assert predecessor_started.wait(timeout=1)
        raise RuntimeError("later worker failed")

    monkeypatch.setattr(main, "process_job", process_job)
    processor = _processor()
    started_at = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="later worker failed"):
            processor.run()
    finally:
        release_predecessor.set()

    assert predecessor_started.is_set()
    assert time.monotonic() - started_at < 1.5


def test_published_failure_wins_before_failed_future_is_done(
    monkeypatch,
):
    predecessor_started = threading.Event()
    release_predecessor = threading.Event()
    failed_callback_entered = threading.Event()
    release_failed_callback = threading.Event()

    def process_job(_chaoxing, _course, job, _job_info, _speed, **_kwargs):
        if job["jobid"] == "predecessor":
            predecessor_started.set()
            assert release_predecessor.wait(timeout=3)
            raise main.StudyCancelled()
        assert predecessor_started.wait(timeout=1)
        raise RuntimeError("later worker failed")

    def job_done(_course, _point, job, result):
        if job["jobid"] == "later-failure" and result == "failed":
            failed_callback_entered.set()
            assert release_failed_callback.wait(timeout=3)

    monkeypatch.setattr(main, "process_job", process_job)
    processor = _processor(job_done_callback=job_done)
    run_errors: list[BaseException] = []

    def invoke():
        try:
            processor.run()
        except BaseException as exc:
            run_errors.append(exc)

    started_at = time.monotonic()
    runner = threading.Thread(target=invoke)
    runner.start()
    try:
        assert predecessor_started.wait(timeout=1)
        assert failed_callback_entered.wait(timeout=1)
        assert processor._worker_stop_event.is_set()
        runner.join(timeout=1)
        assert not runner.is_alive()
    finally:
        release_predecessor.set()
        release_failed_callback.set()

    assert predecessor_started.is_set()
    assert run_errors and isinstance(run_errors[0], RuntimeError)
    assert failed_callback_entered.is_set()
    assert time.monotonic() - started_at < 1.5


def test_blocked_failed_callback_cannot_delay_owner_or_late_video_progress(
    monkeypatch,
):
    predecessor_started = threading.Event()
    release_predecessor = threading.Event()
    failed_callback_entered = threading.Event()
    release_failed_callback = threading.Event()
    video_updates: list[tuple[float, float]] = []

    def process_job(_chaoxing, course, job, _job_info, _speed, **kwargs):
        if job["jobid"] == "predecessor":
            predecessor_started.set()
            assert release_predecessor.wait(timeout=3)
            with pytest.raises(main.StudyCancelled):
                kwargs["progress_callback"](
                    course,
                    job,
                    8.0,
                    10.0,
                )
            return StudyResult.SUCCESS
        assert predecessor_started.wait(timeout=1)
        raise RuntimeError("later worker failed")

    def job_done(_course, _point, job, result):
        if job["jobid"] == "later-failure" and result == "failed":
            failed_callback_entered.set()
            assert release_failed_callback.wait(timeout=3)

    monkeypatch.setattr(main, "process_job", process_job)
    processor = _processor(
        video_progress_callback=lambda _course, _job, progress, total: video_updates.append(
            (progress, total)
        ),
        job_done_callback=job_done,
    )
    run_errors: list[BaseException] = []

    def invoke():
        try:
            processor.run()
        except BaseException as exc:
            run_errors.append(exc)

    started_at = time.monotonic()
    runner = threading.Thread(target=invoke)
    runner.start()
    try:
        assert predecessor_started.wait(timeout=1)
        assert failed_callback_entered.wait(timeout=1)
        assert processor._worker_stop_event.is_set()
        release_predecessor.set()
        runner.join(timeout=1)
        assert not runner.is_alive()
    finally:
        release_predecessor.set()
        release_failed_callback.set()

    assert predecessor_started.is_set()
    assert run_errors and isinstance(run_errors[0], RuntimeError)
    assert failed_callback_entered.is_set()
    assert processor._worker_stop_event.is_set()
    assert video_updates == []
    assert time.monotonic() - started_at < 1.5
