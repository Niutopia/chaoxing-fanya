"""Regression tests for Chaoxing question and option decoding."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from loguru import logger

import api.base as base
from api.base import Account, Chaoxing, StudyResult
from api.decode import decode_questions_info


NEW_OPTION_FORM = """
<form id="synthetic-work">
  <input name="pyFlag" value="1">
  <div class="singleQuesId" data="q-new">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic title</div>
      <ul class="Zy_ulTk">
        <div class="clearfix">
          <span class="num_option" data="A" value="A">A</span>
          <div class="answer_p">alpha <span>nested</span></div>
        </div>
        <div class="clearfix">
          <span class="num_option" data="B" value="B">B</span>
          <div class="answer_p">beta text</div>
        </div>
        <div class="clearfix">
          <span class="num_option" data="C" value="C"><strong>C</strong></span>
          <div class="answer_p">gamma text</div>
        </div>
        <div class="clearfix">
          <span class="num_option" data="D" value="D"></span>
          <div class="answer_p">delta text</div>
        </div>
      </ul>
      <input name="answerq-new_0" value="original-field">
      <input name="unrelated-answer" value="must-not-leak">
    </div>
  </div>
</form>
"""


OLD_OPTION_FORM = """
<form id="synthetic-work">
  <div class="singleQuesId" data="q-old">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic old title</div>
      <ul class="Zy_ulTk">
        <li>A. old alpha</li>
        <li>B. old beta</li>
      </ul>
    </div>
  </div>
</form>
"""


MIXED_OPTION_FORM = """
<form id="synthetic-work">
  <div class="singleQuesId" data="q-mixed">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic mixed title</div>
      <ul class="Zy_ulTk">
        <li>A. legacy alpha</li>
        <li>B. legacy beta</li>
        <div class="clearfix">
          <span class="num_option" data="A">A</span>
          <div class="answer_p">duplicate alpha</div>
        </div>
        <div class="clearfix">
          <span class="num_option" data="layout">layout</span>
          <div class="answer_p">layout block</div>
        </div>
      </ul>
    </div>
  </div>
</form>
"""


MULTI_UL_LEGACY_PRIORITY_FORM = """
<form id="synthetic-work">
  <div class="singleQuesId" data="q-multi-ul">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic multi-ul title</div>
      <ul class="legacy-options">
        <li>A. legacy alpha</li>
        <li>B. legacy beta</li>
      </ul>
      <ul class="Zy_ulTk">
        <div class="clearfix">
          <span class="num_option">A</span>
          <div class="answer_p">new alpha</div>
        </div>
        <div class="clearfix">
          <span class="num_option">B</span>
          <div class="answer_p">new beta</div>
        </div>
      </ul>
    </div>
  </div>
</form>
"""


ORDINARY_LAYOUT_UL_FORM = """
<form id="synthetic-work">
  <div class="singleQuesId" data="q-layout-ul">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic layout title</div>
      <ul class="layout-options">
        <div class="clearfix">
          <span class="num_option">A</span>
          <div class="answer_p">layout text</div>
        </div>
      </ul>
    </div>
  </div>
</form>
"""


DUPLICATE_NEW_BLOCKS_FORM = """
<form id="synthetic-work">
  <div class="singleQuesId" data="q-duplicates">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic duplicate title</div>
      <ul class="Zy_ulTk">
        <div class="clearfix">
          <span class="num_option">A</span>
          <div class="answer_p">same alpha</div>
        </div>
        <div class="clearfix">
          <span class="num_option">A</span>
          <div class="answer_p">same alpha</div>
        </div>
        <div class="clearfix">
          <span class="num_option">B</span>
          <div class="answer_p">same beta</div>
        </div>
      </ul>
    </div>
  </div>
</form>
"""


CONFLICTING_DUPLICATE_NEW_BLOCKS_FORM = """
<form id="synthetic-work">
  <div class="singleQuesId" data="q-conflicting-duplicates">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic conflicting duplicate title</div>
      <ul class="Zy_ulTk">
        <div class="clearfix">
          <span class="num_option">A</span>
          <div class="answer_p">first alpha</div>
        </div>
        <div class="clearfix">
          <span class="num_option">A</span>
          <div class="answer_p">different alpha</div>
        </div>
      </ul>
    </div>
  </div>
</form>
"""


NESTED_DECORATIVE_BLOCK_FORM = """
<form id="synthetic-work">
  <div class="singleQuesId" data="q-nested-decoration">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic nested decoration title</div>
      <ul class="Zy_ulTk">
        <div class="clearfix">
          <span class="num_option">A</span>
          <div class="answer_p">direct alpha</div>
        </div>
        <div class="clearfix layout-wrapper">
          <div class="layout-child">
            <span class="num_option">B</span>
            <div class="answer_p">nested beta</div>
          </div>
        </div>
      </ul>
    </div>
  </div>
</form>
"""


DECORATED_OPTION_FORM = """
<form id="synthetic-work">
  <div class="singleQuesId" data="q-decorated">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic decorated title</div>
      <ul class="Zy_ulTk">
        <div class="clearfix">
          <span class="num_option" data="A">A</span>
          <div class="answer_p">alpha</div>
        </div>
        <div class="clearfix toolbar-block">
          <span class="num_option" data="toolbar">toolbar</span>
        </div>
        <div class="clearfix layout-block">
          <div class="answer_p">not an option</div>
        </div>
        <div class="clearfix">
          <span class="num_option" data=""> </span>
          <div class="answer_p">missing label</div>
        </div>
        <div class="clearfix">
          <span class="num_option" data="B">B</span>
          <div class="answer_p">beta</div>
        </div>
      </ul>
    </div>
  </div>
</form>
"""


MIXED_TEXT_AND_IMAGE_OPTION_FORM = """
<form id="synthetic-work">
  <div class="singleQuesId" data="q-text-image">
    <div class="TiMu" data="0">
      <div class="Zy_TItle">Synthetic text and image title</div>
      <ul class="Zy_ulTk">
        <div class="clearfix">
          <span class="num_option">A</span>
          <div class="answer_p">text alpha</div>
        </div>
        <div class="clearfix">
          <span class="num_option">B</span>
          <div class="answer_p"><img src="only-image.png"></div>
        </div>
      </ul>
    </div>
  </div>
</form>
"""


def _decoded_question(html: str) -> dict:
    result = decode_questions_info(html)
    assert len(result["questions"]) == 1
    return result["questions"][0]


def test_decode_new_num_option_blocks_preserves_labels_text_and_answer_fields():
    question = _decoded_question(NEW_OPTION_FORM)

    assert question["type"] == "single"
    assert question["options"].splitlines() == [
        "A. alpha nested",
        "B. beta text",
        "C. gamma text",
        "D. delta text",
    ]
    assert question["answerField"]["answerq-new"] == ""
    assert question["answerField"]["answertypeq-new"] == "0"
    assert question["answerField"]["answerq-new_0"] == "original-field"
    assert "unrelated-answer" not in question["answerField"]


def test_alternate_options_use_submission_value_when_caption_is_shuffled():
    html = NEW_OPTION_FORM.replace('data="A" value="A">A', 'data="B" value="A">A').replace('data="B" value="B">B', 'data="A" value="B">B')
    options = _decoded_question(html)['options'].splitlines()
    assert 'B. alpha nested' in options
    assert 'A. beta text' in options


def test_decode_old_li_options_remains_unchanged():
    question = _decoded_question(OLD_OPTION_FORM)

    assert question["options"] == "A. old alpha\nB. old beta"


def test_decode_mixed_li_and_new_blocks_uses_li_path_without_duplicates():
    question = _decoded_question(MIXED_OPTION_FORM)

    assert question["options"].splitlines() == [
        "A. legacy alpha",
        "B. legacy beta",
    ]


def test_decode_first_legacy_ul_wins_over_later_zy_ultk():
    question = _decoded_question(MULTI_UL_LEGACY_PRIORITY_FORM)

    assert question["options"].splitlines() == [
        "A. legacy alpha",
        "B. legacy beta",
    ]


def test_decode_does_not_parse_an_ordinary_layout_ul_as_choices():
    question = _decoded_question(ORDINARY_LAYOUT_UL_FORM)

    assert question["options"] == ""


def test_decode_deduplicates_identical_alternate_choice_blocks():
    question = _decoded_question(DUPLICATE_NEW_BLOCKS_FORM)

    assert question["options"].splitlines() == [
        "A. same alpha",
        "B. same beta",
    ]


def test_decode_rejects_alternate_duplicate_label_with_different_text():
    question = _decoded_question(CONFLICTING_DUPLICATE_NEW_BLOCKS_FORM)

    assert question["options"] == ""


@pytest.mark.parametrize('second_value', ['A', ''])
def test_legacy_form_cannot_invent_values_for_invalid_option_controls(second_value):
    html = f'''<form><div class="singleQuesId" data="q"><div class="TiMu" data="0">
      <div class="Zy_TItle">选择一个选项</div><ul>
      <li><span class="num_option" data="A">A</span><a class="after">first</a></li>
      <li><span class="num_option" data="{second_value}">B</span><a class="after">second</a></li>
      </ul></div></div></form>'''
    assert _decoded_question(html)['options'] == ''


def test_decode_ignores_nested_decorative_choice_pairs():
    question = _decoded_question(NESTED_DECORATIVE_BLOCK_FORM)

    assert question["options"] == "A. direct alpha"


def test_decode_new_blocks_require_valid_label_and_text_pair():
    question = _decoded_question(DECORATED_OPTION_FORM)

    assert question["options"].splitlines() == ["A. alpha", "B. beta"]


def test_decode_rejects_mixed_text_and_image_only_alternate_choices():
    question = _decoded_question(MIXED_TEXT_AND_IMAGE_OPTION_FORM)

    assert question["options"] == ""


class _CountingTiku:
    DISABLE = False
    COVER_RATE = 0.0

    def __init__(self, result="A"):
        self.result = result
        self.query_calls = 0
        self.submit_calls = 0

    def query(self, _question):
        self.query_calls += 1
        return self.result

    def get_submit_params(self):
        self.submit_calls += 1
        return "1"

    def judgement_select(self, _answer):
        return True


class _CountingSession:
    def __init__(self):
        self.posts = []

    def get(self, _url, **_kwargs):
        return SimpleNamespace(status_code=200, text="synthetic response")

    def post(self, _url, **kwargs):
        self.posts.append(dict(kwargs.get("data") or {}))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": True, "msg": "saved"},
        )


def _question_with_empty_options(question_type: str, question_id: str) -> dict:
    return {
        "id": question_id,
        "title": "synthetic title must not be logged",
        "options": "",
        "type": question_type,
        "answerField": {
            f"answer{question_id}": "",
            f"answertype{question_id}": "0",
        },
    }


def test_study_work_fails_closed_before_tiku_and_post_for_empty_choice_options(
    monkeypatch,
):
    questions = {
        "questions": [
            _question_with_empty_options("single", "q-missing-single"),
            _question_with_empty_options("multiple", "q-missing-multiple"),
        ],
        "answerwqbid": "q-missing-single,q-missing-multiple,",
    }
    monkeypatch.setattr(
        base,
        "decode_questions_info",
        lambda *_args, **_kwargs: questions,
    )
    tiku = _CountingTiku()
    session = _CountingSession()
    engine = Chaoxing(
        Account("synthetic-user", "synthetic-password"),
        tiku=tiku,
        session=session,
    )
    visible_logs = io.StringIO()
    sink_id = logger.add(visible_logs, format="{message}", level="WARNING")
    try:
        result = engine.study_work(
            {"courseId": "course", "clazzId": "clazz"},
            {"jobid": "work-1", "enc": "enc"},
            {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
        )
    finally:
        logger.remove(sink_id)

    assert result is StudyResult.ERROR
    assert tiku.query_calls == 0
    assert tiku.submit_calls == 0
    assert session.posts == []
    rendered = visible_logs.getvalue()
    assert "选项" in rendered
    assert "synthetic title must not be logged" not in rendered
    assert "synthetic-password" not in rendered


@pytest.mark.parametrize("question_type", ["judgement", "completion"])
def test_study_work_does_not_apply_empty_option_guard_to_non_choice_questions(
    monkeypatch, question_type
):
    question = _question_with_empty_options(question_type, f"q-{question_type}")
    questions = {
        "questions": [question],
        "answerwqbid": f"{question['id']},",
    }
    monkeypatch.setattr(
        base,
        "decode_questions_info",
        lambda *_args, **_kwargs: questions,
    )
    tiku = _CountingTiku("recognized answer")
    session = _CountingSession()
    engine = Chaoxing(
        Account("synthetic-user", "synthetic-password"),
        tiku=tiku,
        session=session,
    )

    result = engine.study_work(
        {"courseId": "course", "clazzId": "clazz"},
        {"jobid": "work-1", "enc": "enc"},
        {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
    )

    assert result is StudyResult.SKIPPED
    assert tiku.query_calls == 1
    assert tiku.submit_calls == 1
    assert len(session.posts) == 1
