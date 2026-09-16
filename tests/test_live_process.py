import threading
from types import SimpleNamespace

import main
import pytest

from api.base import StudyResult
from api.live_process import LiveProcessor
from api.live_process import StudyCancelled, LiveUnavailable
from api.logger import logger
from webapp.task_logging import run_with_task_context


class FakeLive:
    def __init__(self, status, reports):
        self.name = "测试直播"
        self.status = status
        self.reports = list(reports)
        self.prepared = 0
        self.report_calls = 0
        self.final_status = live_status(percentValue=100)

    def get_status(self):
        return self.final_status if self.report_calls else self.status

    def prepare(self):
        self.prepared += 1
        return True

    def do_finish(self):
        self.report_calls += 1
        return self.reports.pop(0)


def live_status(**values):
    data = {
        "duration": 60,
        "timeLongValue": 0,
        "percentValue": 0,
        "liveStatus": 4,
    }
    data.update(values)
    return {"status": True, "temp": {"data": data}}


def test_live_processor_uses_remaining_server_progress(monkeypatch):
    sleeps = []
    live = FakeLive(
        live_status(duration=120, timeLongValue=1),
        reports=[True, True, True],
    )
    monkeypatch.setattr("api.live_process.time.sleep", sleeps.append)

    assert LiveProcessor.run_live(live, speed=1) is True
    assert live.prepared == 1
    assert live.report_calls == 3
    assert sleeps == [30.0, 30.0]


def test_live_processor_scales_heartbeat_interval_without_dropping_final_report(monkeypatch):
    sleeps = []
    live = FakeLive(live_status(duration=60), reports=[True, True, True])
    monkeypatch.setattr("api.live_process.time.sleep", sleeps.append)

    assert LiveProcessor.run_live(live, speed=2) is True
    assert live.report_calls == 3
    assert sleeps == [15.0, 15.0]


def test_live_processor_does_not_claim_success_after_rejected_retry(monkeypatch):
    live = FakeLive(live_status(duration=30), reports=[False, False])
    monkeypatch.setattr("api.live_process.time.sleep", lambda _seconds: None)

    assert LiveProcessor.run_live(live, speed=1) is False
    assert live.report_calls == 2


def test_live_processor_skips_already_completed_replay():
    live = FakeLive(live_status(percentValue=95), reports=[])

    assert LiveProcessor.run_live(live) is True
    assert live.prepared == 0
    assert live.report_calls == 0


def test_live_processor_accepts_an_in_progress_live(monkeypatch):
    live = FakeLive(live_status(duration=30, liveStatus=1), reports=[True, True])
    monkeypatch.setattr("api.live_process.time.sleep", lambda _seconds: None)

    assert LiveProcessor.run_live(live) is True
    assert live.prepared == 1
    assert live.report_calls == 2


def test_live_processor_rejects_non_reviewable_replay():
    live = FakeLive(live_status(ifReview=1), reports=[])

    with pytest.raises(LiveUnavailable, match="未开放回看"):
        LiveProcessor.run_live(live)
    assert live.prepared == 0
    assert live.report_calls == 0


def test_process_job_propagates_live_processor_failure(monkeypatch):
    monkeypatch.setattr(main, "Live", lambda **_kwargs: object())
    monkeypatch.setattr(main.LiveProcessor, "run_live", lambda *_args, **_kwargs: False)
    chaoxing = SimpleNamespace(get_uid=lambda: "uid", session=object())

    result = main.process_job(
        chaoxing,
        {"title": "课程", "clazzId": "class", "courseId": "course"},
        {"type": "live", "jobid": "job", "property": {"title": "直播"}},
        {"knowledgeid": "knowledge"},
        1,
        config={},
    )

    assert result is StudyResult.ERROR


def test_process_job_propagates_live_processor_success(monkeypatch):
    monkeypatch.setattr(main, "Live", lambda **_kwargs: object())
    monkeypatch.setattr(main.LiveProcessor, "run_live", lambda *_args, **_kwargs: True)
    chaoxing = SimpleNamespace(get_uid=lambda: "uid", session=object())

    result = main.process_job(
        chaoxing,
        {"title": "课程", "clazzId": "class", "courseId": "course"},
        {"type": "live", "jobid": "job", "property": {"title": "直播"}},
        {"knowledgeid": "knowledge"},
        1,
        config={},
    )

    assert result is StudyResult.SUCCESS


def test_process_job_stops_waiting_for_noncooperative_live_worker_on_cancel(
    monkeypatch,
):
    release = threading.Event()
    started = threading.Event()

    def blocked_run_live(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2)
        return True

    monkeypatch.setattr(main, "Live", lambda **_kwargs: object())
    monkeypatch.setattr(main.LiveProcessor, "run_live", blocked_run_live)
    chaoxing = SimpleNamespace(get_uid=lambda: "uid", session=object())
    cancel_event = threading.Event()
    captured = []

    def invoke():
        try:
            main.process_job(
                chaoxing,
                {"title": "course", "clazzId": "class", "courseId": "course"},
                {"type": "live", "jobid": "job", "property": {"title": "live"}},
                {"knowledgeid": "knowledge"},
                1,
                config={"cancel_event": cancel_event},
            )
        except BaseException as exc:
            captured.append(exc)

    caller = threading.Thread(target=invoke)
    caller.start()
    assert started.wait(timeout=1)
    cancel_event.set()
    caller.join(timeout=0.5)
    release.set()
    caller.join(timeout=1)

    assert not caller.is_alive()
    assert len(captured) == 1
    assert isinstance(captured[0], StudyCancelled)


def test_live_worker_inherits_task_logging_context(monkeypatch):
    secret = "LIVESECRET"
    seen: list[tuple[str, str | None]] = []

    def run_live(*_args, **_kwargs):
        logger.info("live secret {}", secret)
        return True

    monkeypatch.setattr(main, "Live", lambda **_kwargs: object())
    monkeypatch.setattr(main.LiveProcessor, "run_live", run_live)
    chaoxing = SimpleNamespace(get_uid=lambda: "uid", session=object())
    sink_id = logger.add(
        lambda message: seen.append(
            (message.record["message"], message.record["extra"].get("task_id"))
        ),
        enqueue=False,
    )
    try:
        run_with_task_context(
            "live-context-task",
            main.process_job,
            chaoxing,
            {"title": "course", "clazzId": "class", "courseId": "course"},
            {"type": "live", "jobid": "job", "property": {"title": "live"}},
            {"knowledgeid": "knowledge"},
            1,
            config={"task_id": "live-context-task"},
            log_secrets=(secret,),
        )
    finally:
        logger.remove(sink_id)

    assert seen
    assert all(secret not in message for message, _task_id in seen)
    assert all(task_id == "live-context-task" for _message, task_id in seen)


def test_upcoming_live_without_duration_is_blocked_before_duration_validation():
    # Sanitized shape observed from the platform for both upcoming lives.
    live = FakeLive({'status': True, 'temp': {'data': {
        'liveStatus': 0, 'ifReview': 1, 'timeLongValue': 0, 'percentValue': 0,
    }}}, reports=[])
    with pytest.raises(LiveUnavailable, match='直播尚未开始') as error:
        LiveProcessor.run_live(live)
    assert '等待老师开启直播' in error.value.next_action
    assert live.prepared == live.report_calls == 0


def test_live_accepted_reports_without_platform_completion_do_not_succeed(monkeypatch):
    live = FakeLive(live_status(duration=30), reports=[True, True])
    live.final_status = live_status(duration=30, percentValue=0)
    monkeypatch.setattr('api.live_process.time.sleep', lambda _: None)
    assert LiveProcessor.run_live(live) is False
    assert '平台尚未确认达标' in live.failure_reason


def test_process_job_retains_specific_blocking_reason(monkeypatch):
    live = FakeLive(live_status(liveStatus=0, duration=None), reports=[])
    monkeypatch.setattr(main, 'Live', lambda **_: live)
    job = {'type': 'live', 'jobid': 'job'}
    result = main.process_job(
        SimpleNamespace(get_uid=lambda: 'uid', session=object()),
        {'title': 'course', 'clazzId': 'class', 'courseId': 'course'},
        job, {'knowledgeid': 'knowledge'}, 1, config={},
    )
    assert result is StudyResult.BLOCKED
    assert job['reason'] == '直播尚未开始'
    assert '等待老师开启直播' in job['next_action']


def test_blocked_live_chapter_is_not_retried(monkeypatch):
    from main import ChapterResult, JobProcessor, ChapterTask
    # Run the real chapter scheduler with a single unavailable chapter.
    calls = []
    monkeypatch.setattr(main, 'process_chapter', lambda *args: calls.append(args) or ChapterResult.BLOCKED)
    processor = JobProcessor(
        SimpleNamespace(), {'courseId': 'course'},
        [ChapterTask(0, {'id': 'chapter', 'title': 'Upcoming live'})],
        {'speed': 1, 'jobs': 1, 'notopen_action': 'retry'},
    )
    processor.run()
    assert len(calls) == 1
    assert len(processor.failed_tasks) == 1
