"""The real task-card page initializes mArg before assigning its JSON data."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from api.base import Chaoxing
from api.decode import decode_course_card


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("initializer", ['""', '{}'])
def test_live_card_uses_json_after_empty_initialization(initializer):
    html = (FIXTURES / "course_card_live.html").read_text().replace('mArg = "";', f'mArg = {initializer};')
    jobs, info = decode_course_card(html)
    assert len(jobs) == 1
    assert jobs[0]["type"] == "live"
    assert jobs[0]["jobid"] == "live-job-1"
    assert jobs[0]["name"] == "第一次直播"
    assert info["knowledgeid"] == "chapter-1"


def test_assignment_text_inside_a_card_title_is_not_executed_or_parsed():
    html = (FIXTURES / "course_card_live.html").read_text().replace('第一次直播', 'JavaScript mArg = example')
    jobs, _ = decode_course_card(html)
    assert jobs[0]["name"] == 'JavaScript mArg = example'


def test_empty_template_page_has_no_jobs():
    assert decode_course_card((FIXTURES / "course_card_empty.html").read_text()) == ([], {})


def test_live_chapter_remains_present_when_following_card_pages_are_empty():
    seen = []

    def get(_url, *, params):
        seen.append(params["num"])
        name = "course_card_live.html" if params["num"] == "0" else "course_card_empty.html"
        return SimpleNamespace(status_code=200, url=_url, text=(FIXTURES / name).read_text())

    engine = Chaoxing(session=SimpleNamespace(get=get))
    engine.rate_limiter = SimpleNamespace(limit_rate=lambda: None)

    def unexpected_empty(*_args):
        pytest.fail("A required live task must not be treated as an empty chapter")

    engine.study_emptypage = unexpected_empty
    jobs, info = engine.get_job_list(
        {"clazzId": "class-1", "courseId": "course-1", "cpi": "cpi-1"},
        {"id": "chapter-1"},
    )
    assert seen == list("0123456")
    assert [job["jobid"] for job in jobs] == ["live-job-1"]
    assert info["knowledgeid"] == "chapter-1"


@pytest.mark.parametrize("rhs", ['{"attachments":', '[]', '"unexpected"'])
def test_malformed_nonempty_card_data_is_still_an_error(rhs):
    with pytest.raises(ValueError):
        decode_course_card('mArg = ""; try { mArg = '+rhs+'; } catch(e) {}')
