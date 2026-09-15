from types import SimpleNamespace

import main

from api.base import StudyResult
from api.live_process import LiveProcessor


class FakeLive:
    def __init__(self, status, reports):
        self.name = "测试直播"
        self.status = status
        self.reports = list(reports)
        self.prepared = 0
        self.report_calls = 0

    def get_status(self):
        return self.status

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

    assert LiveProcessor.run_live(live) is False
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
