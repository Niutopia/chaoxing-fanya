"""Regressions across platform responses, answers, and the real task runner."""

import copy
import json
import threading
from types import SimpleNamespace

import pytest
import requests

import main
from api.answer import CacheDAO, Tiku
from api.base import Chaoxing, StudyResult
from api.decode import decode_course_card, decode_course_list
from webapp.models import AccountAuth, AccountPreferences
from webapp import create_app
from webapp.study_runner import ChaoxingStudyRunner
from webapp.task_manager import TaskManager


COURSE = {"courseId": "c1", "clazzId": "class", "cpi": "cpi", "title": "课程"}
POINT = {"id": "p1", "title": "章节", "has_finished": False}


def response(status=200, text="", payload=None):
    result = requests.Response()
    result.status_code = status
    result.url = "https://mooc1.chaoxing.com/course"
    result._content = (json.dumps(payload) if payload is not None else text).encode()
    return result


@pytest.mark.parametrize("operation", ["courses", "chapters", "cards"])
def test_platform_http_failure_cannot_be_an_empty_success(operation):
    failed = response(503, "temporarily unavailable")
    session = SimpleNamespace(get=lambda *a, **k: failed, post=lambda *a, **k: failed)
    engine = Chaoxing(session=session)
    engine.rate_limiter = SimpleNamespace(limit_rate=lambda **k: None)
    with pytest.raises(Exception, match="503"):
        if operation == "courses":
            engine.get_course_list()
        elif operation == "chapters":
            engine.get_course_point("c1", "class", "cpi")
        else:
            engine.get_job_list(COURSE, POINT)


def test_empty_page_failure_is_not_discarded():
    engine = Chaoxing(session=SimpleNamespace(get=lambda *a, **k: response()))
    engine.rate_limiter = SimpleNamespace(limit_rate=lambda **k: None)
    engine.study_emptypage = lambda *a: StudyResult.ERROR
    with pytest.raises(Exception, match="空页面"):
        engine.get_job_list(COURSE, POINT)


@pytest.mark.parametrize("kind", ["document", "read"])
def test_http_200_with_rejected_job_body_is_a_failure(kind):
    engine = Chaoxing(session=SimpleNamespace(
        get=lambda *a, **k: response(payload={"status": False, "msg": "rejected"})
    ))
    job = {"jobid": "job", "jtoken": "token", "otherinfo": "nodeId_p1-cpi"}
    if kind == "document":
        result = engine.study_document(COURSE, job)
    else:
        result = engine.study_read(COURSE, job, {"knowledgeid": "p1"})
    assert result is StudyResult.ERROR


@pytest.mark.parametrize("payload", [{"isPassed": True}, {"status": "true"}, {"success": 1}])
def test_document_recognizes_successful_platform_reports(payload):
    engine = Chaoxing(session=SimpleNamespace(get=lambda *a, **k: response(payload=payload)))
    result = engine.study_document(COURSE, {"jobid": "job", "jtoken": "token", "otherinfo": "nodeId_p1-cpi"})
    assert result is StudyResult.SUCCESS


@pytest.mark.parametrize("passed,expected", [("false", False), (False, False), ("true", True), (True, True)])
def test_video_progress_normalizes_platform_boolean_strings(passed, expected):
    engine = Chaoxing(session=SimpleNamespace(get=lambda *a, **k: response(payload={"isPassed": passed})))
    engine.video_log_limiter = SimpleNamespace(limit_rate=lambda **k: None)
    engine.get_uid = lambda: "uid"
    job = {"jobid": "j1", "objectid": "o1", "otherinfo": "", "rt": "1",
           "videoFaceCaptureEnc": "", "attDuration": "", "attDurationEnc": ""}
    assert engine.video_progress_log(engine.session, COURSE, job, {}, "token", 60, 30) == (expected, 200)


def test_already_passed_video_does_not_send_another_report(monkeypatch):
    video = {"status": "success", "dtoken": "token", "duration": 60, "crc": "crc", "key": "key"}
    engine = Chaoxing(session=SimpleNamespace(get=lambda *a, **k: response(payload=video)))
    engine.get_fid = lambda: "fid"
    reports = []

    def report(*args, **kwargs):
        reports.append(args)
        assert len(reports) == 1, "a confirmed video completion was reported again"
        return True, 200

    engine.video_progress_log = report
    assert engine.study_video(COURSE, {"objectid": "o1", "playTime": 0}, {}) is StudyResult.SUCCESS
    assert len(reports) == 1


@pytest.mark.parametrize("pass_on", [None, 4])
def test_video_final_confirmation_is_bounded_and_allows_delayed_success(monkeypatch, pass_on):
    import api.base as base

    video = {"status": "success", "dtoken": "token", "duration": 60, "crc": "crc", "key": "key"}
    engine = Chaoxing(session=SimpleNamespace(get=lambda *a, **k: response(payload=video)))
    engine.get_fid = lambda: "fid"
    reports = []

    def report(*args, **kwargs):
        reports.append(args)
        assert len(reports) <= 8, "video reporting loops forever after reaching its duration"
        return (pass_on is not None and len(reports) >= pass_on), 200

    monkeypatch.setattr(base, "_wait_or_cancel", lambda *a: None)
    engine.video_progress_log = report
    result = engine.study_video(COURSE, {"objectid": "o1", "playTime": 60000}, {})
    assert result is (StudyResult.TIMEOUT if pass_on is None else StudyResult.SUCCESS)
    if pass_on:
        assert len(reports) == pass_on


def test_pretty_printed_card_json_preserves_titles_and_metadata():
    card = {"defaults": {"knowledgeid": "p1"}, "attachments": [{
        "type": "video", "job": True, "jobid": "j1", "mid": "m1",
        "objectId": "o1", "property": {"name": "A video with spaces"},
    }]}
    jobs, info = decode_course_card('<script>var mArg = '+json.dumps(card, indent=2)+';</script>')
    assert len(jobs) == 1
    assert jobs[0]["name"] == "A video with spaces"
    assert info["knowledgeid"] == "p1"


@pytest.mark.parametrize("card", [
    {"type": "video", "job": True, "jobid": "j1"},
    {"type": "unsupported-required-task", "job": True, "jobid": "j1"},
])
def test_unprocessable_required_cards_are_not_silently_dropped(card):
    with pytest.raises(ValueError):
        decode_course_card('mArg = '+json.dumps({"attachments": [card]})+';')


@pytest.mark.parametrize("required", [False, "false", 0])
@pytest.mark.parametrize("kind", ["video", "read"])
def test_optional_attachments_are_not_processed_as_required_tasks(required, kind):
    card = {"type": kind, "job": required, "jobid": "j1", "mid": "m1"}
    jobs, _ = decode_course_card('mArg='+json.dumps({"attachments": [card]})+';')
    assert jobs == []


def test_course_url_cpi_can_be_the_last_query_parameter():
    html = '''<div class="course" id="c" info="i" roleid="3">
      <input class="clazzId" value="class"><input class="courseId" value="c1">
      <a href="/study?courseid=c1&amp;cpi=123"><span class="course-name" title="课程"></span></a>
      <p class="color3" title="老师"></p></div>'''
    assert decode_course_list(html)[0]["cpi"] == "123"


class AnswerProvider(Tiku):
    def __init__(self, cache):
        super().__init__()
        self._cache = cache
        self.name = "fixture"
        self.calls = []

    def _query(self, question):
        self.calls.append(copy.deepcopy(question))
        return "A" if question["options"].startswith("A. correct") else "B"


def test_cache_does_not_reuse_letters_after_options_are_reordered(tmp_path):
    provider = AnswerProvider(CacheDAO(str(tmp_path / "answers.json")))
    first = {"title": "Choose the correct answer", "type": "single", "options": "A. correct\nB. wrong"}
    second = {**first, "options": "A. wrong\nB. correct"}
    assert provider.query(copy.deepcopy(first)) == "A"
    assert provider.query(copy.deepcopy(second)) == "B"
    assert provider.query(copy.deepcopy(first)) == "A"
    assert len(provider.calls) == 2


def test_question_preprocessing_preserves_leading_mathematical_numbers(tmp_path):
    provider = AnswerProvider(CacheDAO(str(tmp_path / "answers.json")))
    provider.query({"title": "2026 - 2025 = ?", "type": "single", "options": "A. correct\nB. wrong"})
    assert provider.calls[0]["title"] == "2026 - 2025 = ?"


def test_legacy_title_only_choice_cache_is_not_trusted(tmp_path):
    cache = CacheDAO(str(tmp_path / "answers.json"))
    cache.add_cache("same title", "A")
    provider = AnswerProvider(cache)
    assert provider.query({"title": "same title", "type": "single", "options": "A. wrong\nB. correct"}) == "B"
    assert len(provider.calls) == 1


class CourseEngine:
    def __init__(self, mode):
        self.mode = mode
        self.calls = 0
        self.rate_limiter = SimpleNamespace(limit_rate=lambda **k: None)

    def login(self, **kwargs):
        return {"status": True}

    def get_course_list(self):
        return [COURSE]

    def get_course_point(self, *args):
        return {"points": [POINT]}

    def get_job_list(self, *args):
        self.calls += 1
        if self.mode == "not_open":
            return [], {"notOpen": True}
        return [{"jobid": "j1", "type": "document"}], {}

    def study_document(self, *args):
        if self.mode == "success":
            return StudyResult.SUCCESS
        if self.mode == "skipped":
            return StudyResult.SKIPPED
        return StudyResult.ERROR


@pytest.mark.parametrize("mode,action", [("error", "retry"), ("not_open", "retry"), ("not_open", "continue"), ("skipped", "retry")])
def test_incomplete_course_never_finishes_as_completed(tmp_path, monkeypatch, mode, action):
    original_init = main.JobProcessor.__init__

    def short_retry(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.max_tries = 2

    monkeypatch.setattr(main.JobProcessor, "__init__", short_retry)
    engine = CourseEngine(mode)
    manager = TaskManager(runner=ChaoxingStudyRunner(tmp_path, engine_factory=lambda **k: engine))
    task = manager.start("a1", ["c1"], AccountPreferences(jobs=1, notopen_action=action), AccountAuth("u", "p", {}))
    try:
        assert manager.wait(task.id, timeout=3), "unopened chapter retry never terminates"
        final = manager.get_snapshot(task.id)
        assert final.state == "failed"
        assert final.stats["completed_courses"] == 0
        assert final.error
    finally:
        if manager.has_active_task("a1"):
            manager.cancel(task.id)
            manager.wait(task.id, timeout=2)


def test_locked_chapter_waits_for_a_running_prerequisite(monkeypatch):
    prerequisite_started = threading.Event()
    locked_seen = threading.Event()
    release = threading.Event()

    def chapter(_engine, _course, point, _speed, _config):
        if point["id"] == "prerequisite":
            prerequisite_started.set()
            assert release.wait(2)
            return main.ChapterResult.SUCCESS
        assert prerequisite_started.wait(1)
        if not release.is_set():
            locked_seen.set()
            return main.ChapterResult.NOT_OPEN
        return main.ChapterResult.SUCCESS

    monkeypatch.setattr(main, "process_chapter", chapter)
    items = [main.ChapterTask(index=i, point={"id": name}) for i, name in enumerate(("prerequisite", "locked"))]
    processor = main.JobProcessor(None, COURSE, items, {"speed": 1, "jobs": 2, "notopen_action": "retry"})
    processor.max_tries = 1
    worker = threading.Thread(target=processor.run)
    worker.start()
    try:
        assert locked_seen.wait(1)
        # Even a one-attempt budget must leave the blocked chapter eligible
        # while its prerequisite is still being completed.
        assert not processor.failed_tasks
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert not processor.failed_tasks
    assert all(item.result is main.ChapterResult.SUCCESS for item in items)


def test_web_account_to_task_to_restart_flow(tmp_path):
    config = {
        "TESTING": True, "DATA_DIR": tmp_path,
        "CHAOXING_FACTORY": lambda **k: CourseEngine("success"),
        "CHAOXING_ENGINE_FACTORY": lambda **k: CourseEngine("success"),
    }
    app = create_app(config)
    client = app.test_client()
    created = client.post('/api/accounts', json={"name": "测试账户", "username": "user", "password": "test-password"})
    assert created.status_code == 201
    account_id = created.get_json()["data"]["id"]
    account_path = '/api/accounts/'+account_id
    assert client.post(account_path+'/verify').status_code == 200
    assert client.get(account_path+'/courses').get_json()["data"][0]["courseId"] == "c1"
    assert client.put(account_path+'/preferences', json={"selected_course_ids": ["c1"], "jobs": 1}).status_code == 200
    started = client.post(account_path+'/tasks', json={})
    assert started.status_code == 201
    task_id = started.get_json()["data"]["id"]
    assert app.extensions["task_manager"].wait(task_id, 3)
    task_path = '/api/tasks/'+task_id
    final = client.get(task_path).get_json()["data"]
    assert final["state"] == "completed"
    assert final["stats"]["completed_tasks"] == 1
    assert client.get(task_path+'/logs').get_json()["data"]["items"]
    restored = create_app(config).test_client()
    assert restored.get(task_path).get_json()["data"]["state"] == "completed"
    assert restored.get(account_path+'/preferences').get_json()["data"]["selected_course_ids"] == ["c1"]
    assert restored.delete(account_path).status_code == 204
    assert restored.get('/api/accounts').get_json()["data"] == []


def test_default_data_location_does_not_change_with_launch_directory(tmp_path, monkeypatch):
    import webapp

    monkeypatch.delenv("CHAOXING_DATA_DIR", raising=False)
    monkeypatch.setattr(webapp, "__file__", str(tmp_path/'project/webapp/__init__.py'))
    monkeypatch.chdir(tmp_path)
    first = create_app()
    saved = first.extensions["services"]["store"].create_account("账户", "user", "test-password")
    second_cwd = tmp_path/'another-directory'
    second_cwd.mkdir()
    monkeypatch.chdir(second_cwd)
    second = create_app()
    assert second.config["DATA_DIR"] == tmp_path/'project/data'
    assert second.extensions["services"]["store"].get_account(saved.id) is not None


def test_large_course_tree_survives_reporting_persistence_and_restart(tmp_path):
    # More than 128 chapters and 256 fields occur in an ordinary semester.
    # Include the full courses -> chapters -> jobs depth used by the UI.
    courses = [{"id": f"c{c}", "title": f"课程 {c}", "status": "completed", "chapters": [
        {"id": f"p{p}", "title": f"章节 {p}", "status": "completed", "jobs": [
            {"id": f"j{j}", "title": f"任务 {j}", "status": "completed"}
            for j in range(3)
        ]}
        for p in range(150)
    ]} for c in range(6)]

    def run(context):
        context.reporter.set_courses(courses)
        context.reporter.set_counts(completed_courses=6, total_courses=6)

    app = create_app({"DATA_DIR": tmp_path, "TASK_RUNNER": run})
    store = app.extensions["services"]["store"]
    account = store.create_account("账户", "user", "test-password")
    manager = app.extensions["task_manager"]
    task = manager.start(account.id, ["c0"], AccountPreferences(), AccountAuth("user", "test-password", {}))
    assert manager.wait(task.id, 10)
    assert manager.get_details(task.id).courses == courses
    path = '/api/tasks/'+task.id+'/details'
    assert app.test_client().get(path).get_json()["data"]["courses"] == courses
    restored = create_app({"DATA_DIR": tmp_path, "TASK_RUNNER": run})
    assert restored.test_client().get(path).get_json()["data"]["courses"] == courses


@pytest.mark.parametrize("fail", [False, True])
def test_real_business_logs_and_errors_survive_api_persistence_and_restart(tmp_path, fail):
    from api.logger import logger

    emitted = threading.Event()
    release = threading.Event()
    progress = "开始学习课程：高等数学；正在处理第 2 章，共 150 章"
    failure = "有 2 门课程尚未完成，请检查失败、未开放或待手动提交的章节"
    password = "local-audit-password-7291"

    def run(context):
        logger.info(progress)
        logger.warning("连接失败，password={}", password)
        emitted.set()
        assert release.wait(5)
        if fail:
            raise RuntimeError(failure)

    config = {"DATA_DIR": tmp_path, "TASK_RUNNER": run}
    app = create_app(config)
    store = app.extensions["services"]["store"]
    account = store.create_account("账户", "user", password)
    manager = app.extensions["task_manager"]
    task = manager.start(account.id, ["c1"], AccountPreferences(), AccountAuth("user", password, {}))
    path = '/api/tasks/'+task.id
    client = app.test_client()
    expected = [progress, "连接失败，password=[redacted]"]
    try:
        assert emitted.wait(2)
        logs = client.get(path+'/logs').get_json()["data"]["items"]
        assert [entry["message"] for entry in logs] == expected
    finally:
        release.set()
        assert manager.wait(task.id, 5)

    expected_error = failure if fail else None
    assert client.get(path).get_json()["data"]["error"] == expected_error
    persisted = store.load_web_tasks(limit=10)[0]
    assert persisted["snapshot"]["error"] == expected_error
    assert [entry["message"] for entry in persisted["logs"]] == expected
    restored = create_app(config).test_client()
    assert restored.get(path).get_json()["data"]["error"] == expected_error
    logs = restored.get(path+'/logs').get_json()["data"]["items"]
    assert [entry["message"] for entry in logs] == expected
