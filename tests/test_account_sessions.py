import pytest

from api.base import Account, Chaoxing
from api.cookies import load_cookie_file, save_cookie_file
from api.live import Live


class FakeResponse:
    status_code = 200
    text = "@success"

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.requested_urls = []

    def get(self, url, **kwargs):
        self.requested_urls.append(url)
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


def test_cookie_file_round_trip(tmp_path):
    path = tmp_path / "cookies.txt"
    cookies = {"_uid": "account-a", "fid": "100"}

    save_cookie_file(cookies, path)

    assert load_cookie_file(path) == cookies
