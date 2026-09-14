import pytest
from requests.cookies import RequestsCookieJar

from api.base import Account, Chaoxing
from api.cookies import account_cookie_path, load_cookie_file, save_cookie_file
from api.live import Live


class FakeResponse:
    def __init__(self, text="@success", payload=None):
        self.status_code = 200
        self.text = text
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON payload")
        return self._payload


class FakeSession:
    def __init__(self, post_response=None, get_responses=None, post_cookies=None):
        self.cookies = RequestsCookieJar()
        self.requested_urls = []
        self.posted_urls = []
        self.post_response = post_response or FakeResponse()
        self.get_responses = list(get_responses or [])
        self.post_cookies = dict(post_cookies or {})

    def post(self, url, **kwargs):
        self.posted_urls.append(url)
        self.cookies.update(self.post_cookies)
        return self.post_response

    def get(self, url, **kwargs):
        self.requested_urls.append(url)
        if self.get_responses:
            return self.get_responses.pop(0)
        return FakeResponse()


@pytest.fixture
def fake_session():
    return FakeSession()


def test_chaoxing_instances_do_not_share_cookie_jars():
    first = Chaoxing(Account("100", "pw-a"))
    second = Chaoxing(Account("200", "pw-b"))
    first.session.cookies.set("_uid", "account-a")
    second.session.cookies.set("_uid", "account-b")
    assert first.session is not second.session
    assert first.session.cookies.get("_uid") == "account-a"
    assert second.session.cookies.get("_uid") == "account-b"


def test_live_uses_the_session_owned_by_its_account(fake_session):
    live = Live(
        attachment={"property": {"streamName": "s", "vdoid": "v"}},
        defaults={"userid": "u"},
        course_id="c",
        session=fake_session,
    )
    live.do_finish()
    assert fake_session.requested_urls[0].startswith("https://zhibo.chaoxing.com/")


def test_live_get_status_uses_the_explicit_session():
    fake_session = FakeSession(get_responses=[FakeResponse(text='{"duration": 42}')])
    live = Live(
        attachment={"property": {"liveId": "live-1", "_jobid": "job-1"}},
        defaults={
            "userid": "u",
            "clazzId": "clazz",
            "knowledgeid": "knowledge",
        },
        course_id="c",
        session=fake_session,
    )

    assert live.get_status() == {"duration": 42}
    assert fake_session.requested_urls[0].startswith("https://mooc1.chaoxing.com/ananas/live/liveinfo?")


def test_password_login_uses_supplied_session_and_cookie_callback():
    fake_session = FakeSession(
        post_response=FakeResponse(payload={"status": True}),
        post_cookies={"_uid": "account-a"},
    )
    updates = []
    chaoxing = Chaoxing(
        Account("100", "pw-a"),
        session=fake_session,
        cookie_update_callback=updates.append,
    )

    assert chaoxing.login() == {"status": True, "msg": "登录成功"}
    assert fake_session.posted_urls == ["https://passport2.chaoxing.com/fanyalogin"]
    assert updates == [{"_uid": "account-a"}]


def test_cookie_login_uses_supplied_session_and_cookie_callback():
    fake_session = FakeSession(post_response=FakeResponse(text="course html"))
    fake_session.cookies.set("_uid", "account-a")
    updates = []
    chaoxing = Chaoxing(
        Account("100", "pw-a"),
        session=fake_session,
        cookie_update_callback=updates.append,
    )

    assert chaoxing.login(login_with_cookies=True) == {"status": True, "msg": "登录成功"}
    assert fake_session.posted_urls == [
        "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
    ]
    assert updates == [{"_uid": "account-a"}]


def test_cookie_file_round_trip(tmp_path):
    path = tmp_path / "cookies.txt"
    cookies = {"_uid": "account-a", "fid": "100"}

    save_cookie_file(cookies, path)

    assert load_cookie_file(path) == cookies


def test_cookie_loader_ignores_malformed_entries_and_missing_file(tmp_path):
    missing = tmp_path / "missing.txt"
    assert load_cookie_file(missing) == {}

    path = tmp_path / "cookies.txt"
    path.write_text("valid=one;malformed;equals=a=b;; =ignored", encoding="utf-8")
    assert load_cookie_file(path) == {"valid": "one", "equals": "a=b"}


def test_account_cookie_paths_are_distinct(tmp_path):
    first = account_cookie_path("100", tmp_path / "cookies.txt")
    second = account_cookie_path("200", tmp_path / "cookies.txt")
    assert first != second
    assert first.parent == second.parent == tmp_path


def test_cli_initialization_keeps_default_cookie_file_and_callback(monkeypatch):
    import main

    class FakeTiku:
        def config_set(self, config):
            self.config = config

        def get_tiku_from_config(self):
            return self

        def init_tiku(self):
            return None

    fake_session = object()
    captured = {}

    def fake_load_cookie_file(*args):
        captured["load_args"] = args
        return {"_uid": "cli"}

    def fake_build_session(cookies):
        captured["cookies"] = cookies
        return fake_session

    def fake_chaoxing(**kwargs):
        captured["chaoxing"] = kwargs
        return kwargs

    monkeypatch.setattr(main, "Tiku", FakeTiku)
    monkeypatch.setattr(main, "load_cookie_file", fake_load_cookie_file)
    monkeypatch.setattr(main, "build_session", fake_build_session)
    monkeypatch.setattr(main, "Chaoxing", fake_chaoxing)

    result = main.init_chaoxing(
        {"username": "100", "password": "pw-a", "use_cookies": False},
        {},
    )

    assert result["session"] is fake_session
    assert captured["load_args"] == ()
    assert captured["cookies"] == {"_uid": "cli"}
    assert captured["chaoxing"]["cookie_update_callback"] is main.save_cookie_file


def test_web_routes_use_distinct_account_cookie_sessions(monkeypatch):
    import app as legacy_app

    class FakeTiku:
        pass

    class FakeChaoxing:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.session = kwargs["session"]
            self.__class__.instances.append(self)

        def login(self, **kwargs):
            self.kwargs["cookie_update_callback"]({"_uid": "updated"})
            return {"status": True, "msg": "登录成功"}

        def get_course_list(self):
            return []

    loaded_paths = []
    built_sessions = []
    saved = []

    def fake_load_cookie_file(path):
        loaded_paths.append(path)
        return {"_uid": str(path)}

    def fake_build_session(cookies):
        session = object()
        built_sessions.append((session, cookies))
        return session

    def fake_save_cookie_file(cookies, path= None):
        saved.append((cookies, path))

    monkeypatch.setattr(legacy_app, "Tiku", FakeTiku)
    monkeypatch.setattr(legacy_app, "Chaoxing", FakeChaoxing)
    monkeypatch.setattr(legacy_app, "load_cookie_file", fake_load_cookie_file, raising=False)
    monkeypatch.setattr(legacy_app, "build_session", fake_build_session, raising=False)
    monkeypatch.setattr(legacy_app, "save_cookie_file", fake_save_cookie_file, raising=False)

    client = legacy_app.app.test_client()
    login_response = client.post(
        "/api/login",
        json={"username": "100", "password": "pw-a", "use_cookies": True},
    )
    courses_response = client.post(
        "/api/courses",
        json={"username": "200", "password": "pw-b", "use_cookies": True},
    )

    assert login_response.status_code == 200
    assert courses_response.status_code == 200
    assert loaded_paths == [account_cookie_path("100"), account_cookie_path("200")]
    assert built_sessions[0][0] is not built_sessions[1][0]
    assert [instance.kwargs["session"] for instance in FakeChaoxing.instances] == [
        built_sessions[0][0],
        built_sessions[1][0],
    ]
    assert all(callable(instance.kwargs["cookie_update_callback"]) for instance in FakeChaoxing.instances)
    assert [path for _, path in saved] == loaded_paths


def test_web_task_initialization_uses_account_cookie_path(monkeypatch):
    import main

    class FakeTiku:
        def config_set(self, config):
            return None

        def get_tiku_from_config(self):
            return self

        def init_tiku(self):
            return None

    loaded_paths = []
    captured = {}

    def fake_load_cookie_file(path):
        loaded_paths.append(path)
        return {"_uid": "web"}

    def fake_build_session(cookies):
        captured["cookies"] = cookies
        return object()

    def fake_chaoxing(**kwargs):
        captured["kwargs"] = kwargs
        return kwargs

    monkeypatch.setattr(main, "Tiku", FakeTiku)
    monkeypatch.setattr(main, "load_cookie_file", fake_load_cookie_file)
    monkeypatch.setattr(main, "build_session", fake_build_session)
    monkeypatch.setattr(main, "Chaoxing", fake_chaoxing)

    cookie_path = account_cookie_path("200")
    result = main.init_chaoxing(
        {
            "username": "200",
            "password": "pw-b",
            "use_cookies": False,
            "cookie_path": cookie_path,
        },
        {},
    )

    assert loaded_paths == [cookie_path]
    assert result["cookie_update_callback"] is not main.save_cookie_file
    assert callable(result["cookie_update_callback"])
    assert captured["cookies"] == {"_uid": "web"}
