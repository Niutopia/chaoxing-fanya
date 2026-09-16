"""Round-five regressions for course isolation and worker cancellation gates."""

from __future__ import annotations

import threading
from types import SimpleNamespace

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


def test_process_course_reuses_common_config_without_stale_worker_gate():
    """Each course gets a fresh processor callback scope from one config."""

    processed: list[str] = []
    events: list[tuple[str, str]] = []

    class Engine:
        rate_limiter = SimpleNamespace(limit_rate=lambda **_kwargs: None)

        def get_course_point(self, course_id, _clazz_id, _cpi):
            return {
                "points": [
                    {
                        "id": f"{course_id}-chapter",
                        "title": f"{course_id} chapter",
                        "has_finished": False,
                    }
                ]
            }

        def get_job_list(self, _course, point):
            return (
                [{"type": "document", "jobid": f"{point['id']}-job"}],
                {},
            )

        def study_document(self, course, job):
            processed.append(f"{course['courseId']}:{job['jobid']}")
            return StudyResult.SUCCESS

    common_config = _processor_config(
        chapter_start_callback=lambda course, point: events.append(
            ("chapter_start", course["courseId"])
        ),
        job_list_callback=lambda course, point, jobs, info: events.append(
            ("job_list", course["courseId"])
        ),
        chapter_done_callback=lambda course, point: events.append(
            ("chapter_done", course["courseId"])
        ),
        course_done_callback=lambda course: events.append(
            ("course_done", course["courseId"])
        ),
        course_failed_callback=lambda course: events.append(
            ("course_failed", course["courseId"])
        ),
    )
    courses = [
        {"courseId": "course-a", "clazzId": "class-a", "cpi": "cpi-a"},
        {"courseId": "course-b", "clazzId": "class-b", "cpi": "cpi-b"},
    ]

    engine = Engine()
    main.process_course(engine, courses[0], common_config)
    main.process_course(engine, courses[1], common_config)

    assert processed == ["course-a:course-a-chapter-job", "course-b:course-b-chapter-job"]
    assert [event for event in events if event[0] == "course_done"] == [
        ("course_done", "course-a"),
        ("course_done", "course-b"),
    ]
    assert [event for event in events if event[0] == "chapter_done"] == [
        ("chapter_done", "course-a"),
        ("chapter_done", "course-b"),
    ]
    assert "_worker_stop_event" not in common_config


def test_retry_worker_stop_drain_barrier_does_not_leave_a_late_requeue(monkeypatch):
    """A retry put racing stop/drain is either admitted before stop or dropped."""

    retry_put_entered = threading.Event()
    release_retry_put = threading.Event()
    drain_entered = threading.Event()
    release_drain = threading.Event()
    cancel_event = threading.Event()
    run_errors: list[BaseException] = []

    def always_retry(_chaoxing, _course, _point, _speed, _config):
        return main.ChapterResult.ERROR

    monkeypatch.setattr(main, "process_chapter", always_retry)
    processor = main.JobProcessor(
        SimpleNamespace(),
        {"courseId": "course"},
        [main.ChapterTask(index=0, point={"id": "chapter"})],
        _processor_config(cancel_event=cancel_event),
    )

    original_put = processor.task_queue.put
    put_count = 0

    def gated_put(task, *args, **kwargs):
        nonlocal put_count
        put_count += 1
        if put_count == 2:
            retry_put_entered.set()
            assert release_retry_put.wait(timeout=2)
        return original_put(task, *args, **kwargs)

    processor.task_queue.put = gated_put
    original_drain = processor._drain_pending_tasks

    def gated_drain():
        drain_entered.set()
        assert release_drain.wait(timeout=2)
        original_drain()

    processor._drain_pending_tasks = gated_drain

    def invoke():
        try:
            processor.run()
        except BaseException as exc:
            run_errors.append(exc)

    runner = threading.Thread(target=invoke)
    runner.start()
    assert retry_put_entered.wait(timeout=1)
    cancel_event.set()

    # The unfixed processor can stop and enter drain while the retry put is
    # parked.  The fixed lifecycle gate keeps stop/drain behind that put.
    stopped_before_put = drain_entered.wait(timeout=0.8)
    if stopped_before_put:
        release_drain.set()
        runner.join(timeout=1)
        assert not runner.is_alive()
        release_retry_put.set()
    else:
        release_retry_put.set()
        assert drain_entered.wait(timeout=1)
        release_drain.set()
    runner.join(timeout=2)

    assert not runner.is_alive()
    assert run_errors and isinstance(run_errors[0], main.StudyCancelled)
    assert processor.task_queue.qsize() == 0
    assert processor.retry_queue.qsize() == 0
    assert processor.task_queue.unfinished_tasks == 0


def test_retry_worker_still_requeues_a_normal_retry(monkeypatch):
    attempts: list[int] = []

    def fail_once(_chaoxing, _course, _point, _speed, _config):
        attempts.append(1)
        return (
            main.ChapterResult.ERROR
            if len(attempts) == 1
            else main.ChapterResult.SUCCESS
        )

    monkeypatch.setattr(main, "process_chapter", fail_once)
    processor = main.JobProcessor(
        SimpleNamespace(),
        {"courseId": "course"},
        [main.ChapterTask(index=0, point={"id": "chapter"})],
        _processor_config(),
    )

    processor.run()

    assert len(attempts) == 2
    assert processor.task_queue.qsize() == 0
    assert processor.retry_queue.qsize() == 0
    assert processor.task_queue.unfinished_tasks == 0


def test_late_video_progress_is_blocked_after_another_worker_fails(monkeypatch):
    late_started = threading.Event()
    release_late_worker = threading.Event()
    late_finished = threading.Event()
    video_updates: list[tuple[float, float]] = []
    run_errors: list[BaseException] = []

    def fail_or_report(_chaoxing, course, point, _speed, config):
        if point["id"] == "failing":
            assert late_started.wait(timeout=2)
            raise RuntimeError("worker failed")
        late_started.set()
        assert release_late_worker.wait(timeout=2)
        try:
            config["video_progress_callback"](
                course,
                {"id": "video-job", "type": "video"},
                7.0,
                10.0,
            )
        finally:
            late_finished.set()
        return main.ChapterResult.SUCCESS

    monkeypatch.setattr(main, "process_chapter", fail_or_report)
    processor = main.JobProcessor(
        SimpleNamespace(),
        {"courseId": "course", "title": "course"},
        [
            main.ChapterTask(index=0, point={"id": "failing"}),
            main.ChapterTask(index=1, point={"id": "late-video"}),
        ],
        _processor_config(
            jobs=2,
            video_progress_callback=lambda _course, _job, progress, total: video_updates.append(
                (progress, total)
            ),
        ),
    )

    def invoke():
        try:
            processor.run()
        except BaseException as exc:
            run_errors.append(exc)

    runner = threading.Thread(target=invoke)
    runner.start()
    assert late_started.wait(timeout=1)
    runner.join(timeout=1.5)
    assert not runner.is_alive()

    release_late_worker.set()
    assert late_finished.wait(timeout=1)

    assert run_errors and isinstance(run_errors[0], RuntimeError)
    assert video_updates == []


def test_video_progress_from_a_sibling_job_is_blocked_after_job_failure(monkeypatch):
    """A failed chapter job closes the gate before a sibling video reports."""

    video_started = threading.Event()
    release_video = threading.Event()
    video_finished = threading.Event()
    video_updates: list[tuple[float, float]] = []
    run_errors: list[BaseException] = []

    class Engine:
        rate_limiter = SimpleNamespace(limit_rate=lambda **_kwargs: None)

        def get_job_list(self, _course, _point):
            return (
                [
                    {"type": "document", "jobid": "failed-job"},
                    {"type": "video", "jobid": "late-video-job"},
                ],
                {},
            )

    def fail_or_report(_chaoxing, course, job, _job_info, _speed, **kwargs):
        if job["jobid"] == "failed-job":
            assert video_started.wait(timeout=2)
            raise RuntimeError("job failed")
        video_started.set()
        assert release_video.wait(timeout=2)
        try:
            kwargs["progress_callback"](course, job, 8.0, 10.0)
        finally:
            video_finished.set()
        return StudyResult.SUCCESS

    monkeypatch.setattr(main, "process_job", fail_or_report)
    processor = main.JobProcessor(
        Engine(),
        {"courseId": "course", "title": "course"},
        [main.ChapterTask(index=0, point={"id": "chapter"})],
        _processor_config(
            video_progress_callback=lambda _course, _job, progress, total: video_updates.append(
                (progress, total)
            )
        ),
    )

    def invoke():
        try:
            processor.run()
        except BaseException as exc:
            run_errors.append(exc)

    runner = threading.Thread(target=invoke)
    runner.start()
    assert video_started.wait(timeout=1)
    stop_reached = processor._worker_stop_event.wait(timeout=1)
    release_video.set()
    assert video_finished.wait(timeout=1)
    runner.join(timeout=1.5)

    assert not runner.is_alive()
    assert stop_reached
    assert run_errors and isinstance(run_errors[0], RuntimeError)
    assert video_updates == []


def test_video_progress_is_reported_before_processor_stop():
    video_updates: list[tuple[float, float]] = []

    def report_progress(_chaoxing, course, point, _speed, config):
        config["video_progress_callback"](
            course,
            {"id": "video-job", "type": "video"},
            2.0,
            10.0,
        )
        return main.ChapterResult.SUCCESS

    original = main.process_chapter
    try:
        main.process_chapter = report_progress
        processor = main.JobProcessor(
            SimpleNamespace(),
            {"courseId": "course"},
            [main.ChapterTask(index=0, point={"id": "video"})],
            _processor_config(
                video_progress_callback=lambda _course, _job, progress, total: video_updates.append(
                    (progress, total)
                )
            ),
        )
        processor.run()
    finally:
        main.process_chapter = original

    assert video_updates == [(2.0, 10.0)]


def test_later_job_failure_closes_gate_before_earlier_video_reports(monkeypatch):
    """A later submitted failure blocks progress from an earlier job."""

    video_started = threading.Event()
    failing_started = threading.Event()
    failed_job_done = threading.Event()
    release_video = threading.Event()
    video_finished = threading.Event()
    video_updates: list[tuple[float, float]] = []
    job_events: list[tuple[str, str]] = []
    run_errors: list[BaseException] = []

    class Engine:
        rate_limiter = SimpleNamespace(limit_rate=lambda **_kwargs: None)

        def get_job_list(self, _course, _point):
            return (
                [
                    {"type": "video", "jobid": "video-job"},
                    {"type": "document", "jobid": "failed-job"},
                ],
                {},
            )

    def fail_or_report(_chaoxing, course, job, _job_info, _speed, **kwargs):
        if job["jobid"] == "video-job":
            video_started.set()
            assert release_video.wait(timeout=2)
            try:
                kwargs["progress_callback"](course, job, 8.0, 10.0)
            finally:
                video_finished.set()
            return StudyResult.SUCCESS
        failing_started.set()
        raise RuntimeError("job failed")

    def job_done(_course, _point, job, result):
        job_events.append((job["jobid"], result))
        if job["jobid"] == "failed-job":
            failed_job_done.set()

    monkeypatch.setattr(main, "process_job", fail_or_report)
    processor = main.JobProcessor(
        Engine(),
        {"courseId": "course", "title": "course"},
        [main.ChapterTask(index=0, point={"id": "chapter"})],
        _processor_config(
            video_progress_callback=lambda _course, _job, progress, total: video_updates.append(
                (progress, total)
            ),
            job_done_callback=job_done,
        ),
    )

    def invoke():
        try:
            processor.run()
        except BaseException as exc:
            run_errors.append(exc)

    runner = threading.Thread(target=invoke)
    runner.start()
    assert video_started.wait(timeout=1)
    assert failing_started.wait(timeout=1)
    assert failed_job_done.wait(timeout=1)
    assert processor._worker_stop_event.wait(timeout=1)

    release_video.set()
    assert video_finished.wait(timeout=1)
    runner.join(timeout=1.5)

    assert not runner.is_alive()
    assert run_errors and isinstance(run_errors[0], RuntimeError)
    assert ("failed-job", "failed") in job_events
    assert video_updates == []


def test_cancel_event_blocks_late_video_progress_before_stop_gate():
    """Cancellation rejects a late progress callback even before stop is set."""

    cancel_event = threading.Event()
    video_updates: list[tuple[float, float]] = []
    processor = main.JobProcessor(
        SimpleNamespace(),
        {"courseId": "course"},
        [],
        _processor_config(
            cancel_event=cancel_event,
            video_progress_callback=lambda _course, _job, progress, total: video_updates.append(
                (progress, total)
            ),
        ),
    )

    cancel_event.set()
    assert not processor._worker_stop_event.is_set()
    try:
        processor.config["video_progress_callback"](
            {"courseId": "course"},
            {"id": "video-job", "type": "video"},
            9.0,
            10.0,
        )
    except main.StudyCancelled:
        pass

    assert video_updates == []
