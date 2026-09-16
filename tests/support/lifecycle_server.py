"""Isolated HTTP acceptance server; only external platform/answer IO is fake.

Run with DATA_DIR and ready-file arguments. The parent creates a dedicated
temporary directory, reused only when testing a restart of that same instance.
The real task manager, runner, scheduler, form parser and submit logic run here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DATA = Path(sys.argv[1]).resolve()
DATA.mkdir(parents=True, exist_ok=True)
os.environ["CHAOXING_DATA_DIR"] = str(DATA)

import httpx
import requests
from werkzeug.serving import make_server

from api.base import Account, Chaoxing
from webapp import create_app

COURSE = {"courseId": "course-acceptance", "clazzId": "class-test",
          "cpi": "cpi-test", "title": "隔离验收课程"}
STATE_FILE = DATA / "platform.json"
LOCK = threading.RLock()
STATE = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {
    "submitted": [], "posts": [], "events": [], "lose_response": True,
}


def record(event, **values):
    with LOCK:
        STATE["events"].append({"event": event, **values})
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(STATE, ensure_ascii=False))
        temporary.replace(STATE_FILE)


def grade_page():
    return ('<title>查看已批阅作业</title><div class="newTestCon">'
            '题量: 1 满分: 100 本次成绩 100 分'
            '<div class="answerScore"><div class="CorrectOrNot">'
            '<span class="marking_dui"></span></div></div></div>')


def work_page(job):
    # Display labels deliberately differ from platform submission values.
    return f'''<div class="newTestCon">待完成 题量: 1 满分: 100</div>
      <form><input name="jobid" value="{job}">
      <div class="singleQuesId" data="q-{job}"><div class="TiMu" data="0">
        <div class="Zy_TItle">隔离测试题 {job}</div><ul class="Zy_ulTk">
        <div class="clearfix"><span class="num_option" data="D">A</span><div class="answer_p">正确内容</div></div>
        <div class="clearfix"><span class="num_option" data="B">B</span><div class="answer_p">错误内容</div></div>
        </ul><input name="answerq-{job}" value=""></div></div></form>'''


def response(url, text="", payload=None):
    return SimpleNamespace(status_code=200, url=url, text=text,
                           json=lambda: payload, raise_for_status=lambda: None)


class PlatformSession:
    def __init__(self):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.cookies.set("_uid", "acceptance-user")

    def close(self):
        pass

    def get(self, url, **kwargs):
        params = kwargs.get("params", {})
        if url.endswith("/knowledge/cards"):
            chapter = params["knowledgeid"]
            data = {"defaults": {"knowledgeid": chapter, "ktoken": "signed-test-token", "cpi": "cpi-test"},
                    "attachments": [{"type": "workid", "jobid": f"work-{chapter}", "enc": "signed-test-enc", "job": True}]}
            return response(url, "mArg=" + json.dumps(data) if params["num"] == 0 else "mArg=$mArg;")
        assert url.endswith("/api/work"), url
        job = params["jobid"]
        with LOCK:
            submitted = job in STATE["submitted"]
            record("read", job=job, submitted=submitted)
        return response(url, grade_page() if submitted else work_page(job))

    def post(self, url, **kwargs):
        assert url.endswith("/work/addStudentWorkNew"), url
        data = kwargs["data"]
        job = data["jobid"]
        assert data[f"answerq-{job}"] == "D", "Must submit the actual option value"
        assert data["pyFlag"] == "", "Must submit a fully covered form"
        with LOCK:
            assert job not in STATE["submitted"], "Duplicate platform submission"
            STATE["posts"].append(job)
            STATE["submitted"].append(job)
            lose = STATE["lose_response"]
            STATE["lose_response"] = False
            record("submit", job=job, response_lost=lose)
        if lose:
            raise requests.exceptions.ReadTimeout("synthetic lost response signed-test-enc")
        return response(url, payload={"status": True})


class AnswerProvider:
    DISABLE = False
    COVER_RATE = 1.0

    def query(self, question):
        if question["id"] == "q-work-second":
            record("answer_wait", job="work-second")
            # Intentionally non-cooperative external call. Real submission
            # cancellation gates must prevent a late response from posting.
            deadline = time.monotonic() + 45
            while not (DATA / "release-answer").exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("acceptance answer gate timed out")
                time.sleep(0.02)
            record("answer_return", job="work-second")
        return "正确内容"

    def get_submit_params(self):
        return ""


class PlatformEngine(Chaoxing):
    def __init__(self, auth, cookie_update_callback=None, common_config=None, **_):
        config = common_config or {}
        super().__init__(Account(auth.username, auth.password), tiku=AnswerProvider(),
                         session=PlatformSession(), cookie_update_callback=cookie_update_callback,
                         cancel_event=config.get("cancel_event"), task_id=config.get("task_id"))
        self.rate_limiter = SimpleNamespace(limit_rate=lambda **_: None)

    def login(self, login_with_cookies=False):
        self._notify_cookie_update()
        return {"status": True}

    def get_course_list(self):
        return [dict(COURSE)]

    def get_course_point(self, *_):
        # Keep the chapter list stale on purpose. The work page must be
        # checked again even when platform chapter flags lag behind a POST.
        return {"points": [{"id": item, "title": title, "has_finished": False, "jobCount": 1}
                           for item, title in [("first", "第一章"), ("second", "第二章")]]}

    def get_job_list(self, _course, point):
        return ([{"type": "workid", "jobid": f"work-{point['id']}", "enc": "signed-test-enc"}],
                {"knowledgeid": point["id"], "ktoken": "signed-test-token", "cpi": "cpi-test"})


app = create_app({"DATA_DIR": DATA, "CHAOXING_FACTORY": PlatformEngine,
                  "CHAOXING_ENGINE_FACTORY": PlatformEngine,
                  "ANSWER_CONNECTION_TRANSPORT": httpx.MockTransport(
                      lambda request: httpx.Response(200, json=(
                          {"data": [{"id": "acceptance"}]} if request.method == "GET"
                          else {"choices": [{"message": {"content": "OK"}}]})))})
server = make_server("127.0.0.1", 0, app, threaded=True)
Path(sys.argv[2]).write_text(f"http://127.0.0.1:{server.server_port}")
server.serve_forever()
