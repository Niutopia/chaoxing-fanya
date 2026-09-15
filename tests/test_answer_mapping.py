from __future__ import annotations

from types import SimpleNamespace

import pytest

import api.base as base
from api.answer import AI, SiliconFlow
from api.base import Account, Chaoxing, StudyResult, _resolve_choice_answer


class _AnswerTiku:
    DISABLE = False
    COVER_RATE = 0.0

    def __init__(self, result):
        self.result = result

    def query(self, _question):
        return self.result

    def get_submit_params(self):
        return ""

    def judgement_select(self, answer):
        return str(answer).strip().lower() in {"true", "正确", "对", "t"}


class _WorkSession:
    def __init__(self):
        self.posts = []

    def get(self, _url, **_kwargs):
        return SimpleNamespace(status_code=200, text="work html")

    def post(self, _url, **kwargs):
        self.posts.append(dict(kwargs.get("data") or {}))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": True, "msg": "saved"},
        )


def _run_work(monkeypatch, question, result):
    form = {"questions": [question], "answerwqbid": f"{question['id']},"}
    monkeypatch.setattr(
        base,
        "decode_questions_info",
        lambda *_args, **_kwargs: form,
    )
    session = _WorkSession()
    engine = Chaoxing(
        Account("test-user", "test-password"),
        tiku=_AnswerTiku(result),
        session=session,
    )
    outcome = engine.study_work(
        {"courseId": "course", "clazzId": "clazz"},
        {"jobid": "work-1", "enc": "enc"},
        {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
    )
    assert outcome is StudyResult.SUCCESS
    assert len(session.posts) == 1
    return session.posts[0]


def test_none_answer_uses_type_aware_random_fallback(monkeypatch):
    posted = _run_work(
        monkeypatch,
        {
            "id": "q1",
            "title": "question",
            "options": "A. first\nB. second",
            "type": "single",
            "answerField": {"answerq1": "", "answertypeq1": "0"},
        },
        None,
    )

    assert posted["answerq1"] in {"A", "B"}


@pytest.mark.parametrize(
    ("result", "options", "multiple", "expected"),
    [
        ("B", "A. alpha\nB. cat", False, "B"),
        ("cat", "A. alpha\nB. cat", False, "B"),
        ("cat dog", "A. cat dog\nB. dog", False, "A"),
        ("cat", "A. cat\nB. dog\nC. cow", True, "A"),
        ("A\nC", "A. cat\nB. dog\nC. cow", True, "AC"),
        ("x", "A. x\nB. y", False, "A"),
        ("PYTHON", "A. Python\nB. Java", False, "A"),
        ("中文", "A. 中文\nB. 英文", False, "A"),
    ],
)
def test_choice_mapping_requires_labels_or_complete_text(
    result, options, multiple, expected
):
    assert _resolve_choice_answer(result, options, multiple=multiple) == expected


def test_completion_answers_fill_existing_indexed_fields(monkeypatch):
    posted = _run_work(
        monkeypatch,
        {
            "id": "q1",
            "title": "fill",
            "options": "",
            "type": "completion",
            "answerField": {
                "answerq1": "",
                "answerq1_0": "",
                "answerq1_1": "",
                "answertypeq1": "2",
            },
        },
        ["first", "second"],
    )

    assert posted["answerq1"] == "first\nsecond"
    assert posted["answerq1_0"] == "first"
    assert posted["answerq1_1"] == "second"


def test_ai_prompt_keeps_dynamic_option_labels_through_e(monkeypatch):
    question = {
        "type": "single",
        "title": "five choices",
        "options": "A. alpha\nB. beta\nC. gamma\nD. delta\nE. epsilon",
    }
    messages = AI()._build_messages(question)
    system = messages[0]["content"]
    user = messages[1]["content"]

    assert "仅填A/B/C/D" not in system
    assert "本题合法选项标签：A、B、C、D、E" in system
    assert "A. alpha" in user
    assert "E. epsilon" in user

    class _CompletionSession:
        def __init__(self):
            self.payload = None

        def post(self, _url, **kwargs):
            self.payload = kwargs["json"]
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "choices": [{"message": {"content": '{"Answer": ["E"]}'}}]
                },
            )

    session = _CompletionSession()
    provider = SiliconFlow()
    provider.api_endpoint = "http://answer.invalid/v1/chat/completions"
    provider.api_key = ""
    provider.model_name = "test-model"
    provider.min_interval = 0
    provider.timeout = 1
    provider.max_retries = 1
    provider.retry_delay = 0
    provider._session = session

    assert provider._query(question) == "E"
    assert "本题合法选项标签：A、B、C、D、E" in session.payload["messages"][0]["content"]
    assert "A. alpha" in session.payload["messages"][1]["content"]
    assert "E. epsilon" in session.payload["messages"][1]["content"]
