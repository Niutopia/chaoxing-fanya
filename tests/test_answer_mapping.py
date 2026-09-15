from __future__ import annotations

import signal
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


def _run_work(monkeypatch, question, result, *, cover_rate=0.0):
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
    engine.tiku.COVER_RATE = cover_rate
    outcome = engine.study_work(
        {"courseId": "course", "clazzId": "clazz"},
        {"jobid": "work-1", "enc": "enc"},
        {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
    )
    assert outcome is StudyResult.SUCCESS
    assert len(session.posts) == 1
    return session.posts[0]


def _call_with_timeout(callback, seconds=0.5):
    """Fail fast if an option-label collision regresses into an infinite loop."""

    def _timeout(_signum, _frame):
        raise TimeoutError("option parsing did not terminate")

    previous_handler = signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return callback()
    except TimeoutError as exc:
        pytest.fail(str(exc))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _silicon_prompt_messages(question, answer="A"):
    class _PromptSession:
        def __init__(self):
            self.payload = None

        def post(self, _url, **kwargs):
            self.payload = kwargs["json"]
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "choices": [{"message": {"content": f'{{"Answer": ["{answer}"]}}'}}]
                },
            )

    provider = SiliconFlow()
    provider.api_endpoint = "http://answer.invalid/v1/chat/completions"
    provider.api_key = ""
    provider.model_name = "test-model"
    provider.min_interval = 0
    provider.timeout = 1
    provider.max_retries = 1
    provider.retry_delay = 0
    session = _PromptSession()
    provider._session = session

    assert provider._query(question) == answer
    return session.payload["messages"]


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


def test_all_empty_completion_is_saved_as_uncovered(monkeypatch):
    question = {
        "id": "q1",
        "title": "empty fill",
        "options": "",
        "type": "completion",
        "answerField": {
            "answerq1": "old",
            "answerq1_0": "old-0",
            "answerq1_1": "old-1",
            "answertypeq1": "2",
        },
    }

    posted = _run_work(monkeypatch, question, ["", ""], cover_rate=0.5)

    assert question["answerSourceq1"] == "random"
    assert posted["pyFlag"] == "1"
    assert posted["answerq1"] == ""
    assert posted["answerq1_0"] == ""
    assert posted["answerq1_1"] == ""


@pytest.mark.parametrize(
    "prefix",
    ["A.", "A)", "A:", "A、", "[A]", "【A】", " A : "],
)
def test_explicit_prefix_forms_map_like_prompt_labels(prefix):
    options = f"{prefix} alpha\nB. beta"

    assert _resolve_choice_answer("alpha", options, multiple=False) == "A"
    messages = AI()._build_messages(
        {"type": "single", "title": "prefix", "options": options}
    )
    assert "A. alpha" in messages[1]["content"]


def test_lowercase_excel_label_is_not_preserved_as_a_label():
    options = "aa. alpha\nbb. beta"

    assert _resolve_choice_answer("alpha", options, multiple=False) == "A"
    assert "A. alpha" in AI()._build_messages(
        {"type": "single", "title": "case", "options": options}
    )[1]["content"]


def test_lowercase_words_with_punctuation_get_generated_labels():
    options = "cat, dog\nB. other"

    assert _resolve_choice_answer("cat, dog", options, multiple=False) == "A"
    assert _resolve_choice_answer("cat", options, multiple=False) == ""
    assert "A. cat, dog" in AI()._build_messages(
        {"type": "single", "title": "words", "options": options}
    )[1]["content"]


def test_chinese_enumeration_punctuation_is_normalized_for_text_matching():
    assert (
        _resolve_choice_answer("甲乙", "A. 甲、乙\nB. 丙", multiple=False)
        == "A"
    )


def test_conflicting_prompt_labels_terminate_and_use_first_unused_label():
    question = {
        "type": "single",
        "title": "conflict",
        "options": "B. first\nsecond",
    }

    messages = _call_with_timeout(lambda: AI()._build_messages(question))

    assert "B. first" in messages[1]["content"]
    assert "A. second" in messages[1]["content"]
    assert "本题合法选项标签：B、A" in messages[0]["content"]
    assert _resolve_choice_answer("second", question["options"], multiple=False) == "A"

    silicon_messages = _call_with_timeout(
        lambda: _silicon_prompt_messages(question)
    )
    assert "A. second" in silicon_messages[1]["content"]
    assert "本题合法选项标签：B、A" in silicon_messages[0]["content"]


def test_duplicate_prompt_labels_are_reassigned_without_hanging():
    options = "B. first\nB. second\nC. third"

    messages = _call_with_timeout(
        lambda: AI()._build_messages(
            {"type": "single", "title": "duplicate", "options": options}
        )
    )

    assert "B. first" in messages[1]["content"]
    assert "A. second" in messages[1]["content"]
    assert "C. third" in messages[1]["content"]
    assert _resolve_choice_answer("second", options, multiple=False) == "A"

    silicon_messages = _call_with_timeout(
        lambda: _silicon_prompt_messages(
            {"type": "single", "title": "duplicate", "options": options}
        )
    )
    assert "A. second" in silicon_messages[1]["content"]
    assert "C. third" in silicon_messages[1]["content"]


def test_unknown_multi_segment_does_not_keep_a_partial_match():
    assert (
        _resolve_choice_answer(
            "cat|nonsense", "A. cat\nB. dog", multiple=True
        )
        == ""
    )


def test_single_answer_with_multiple_requested_values_is_rejected():
    options = "A. alpha\nB. beta\nC. gamma\nD. AC"

    assert _resolve_choice_answer(["alpha", "beta"], options, multiple=False) == ""
    assert _resolve_choice_answer("A,C", options, multiple=False) == ""


def test_single_answer_with_ambiguous_and_valid_segments_is_rejected():
    options = "A. same\nB. same\nC. other"

    assert _resolve_choice_answer("same, other", options, multiple=False) == ""


def test_compact_multi_labels_are_case_insensitive_and_strict():
    options = "A. alpha\nB. beta\nC. gamma"

    assert _resolve_choice_answer("AC", options, multiple=True) == "AC"
    assert _resolve_choice_answer("ac", options, multiple=True) == "AC"
    assert _resolve_choice_answer("AABB", options, multiple=True) == "AB"
    assert (
        _resolve_choice_answer(
            "A,C", "A. alpha\nB. beta\nC. gamma\nD. AC", multiple=True
        )
        == "AC"
    )


def test_exact_multi_letter_label_takes_precedence_over_compact_labels():
    options = "AA. alpha\nA. other"

    assert _resolve_choice_answer("AA", options, multiple=True) == "AA"


def test_multiple_labels_are_deduplicated_in_option_order():
    options = "C. cat\nA. alpha\nB. beta"

    assert _resolve_choice_answer(["A", "C", "A"], options, multiple=True) == "CA"


@pytest.mark.parametrize(
    "options",
    [["alpha", "beta"], ("alpha", "beta")],
)
def test_list_and_tuple_options_have_the_same_mapping(options):
    assert _resolve_choice_answer("alpha", options, multiple=False) == "A"


def test_set_options_are_stably_sorted_for_prompt_and_mapping():
    options = {"beta", "alpha"}
    question = {"type": "single", "title": "set", "options": options}

    assert _resolve_choice_answer("alpha", options, multiple=False) == "A"
    messages = AI()._build_messages(question)
    assert "A. alpha" in messages[1]["content"]
    assert "B. beta" in messages[1]["content"]


def test_27th_option_uses_aa_for_mapping_and_both_provider_prompts():
    options = [f"item {index}" for index in range(27)]
    question = {"type": "single", "title": "many", "options": options}

    assert _resolve_choice_answer("item 26", options, multiple=False) == "AA"

    ai_messages = AI()._build_messages(question)
    assert "AA. item 26" in ai_messages[1]["content"]
    assert "本题合法选项标签：A、B、C、D、E、F、G、H、I、J、K、L、M、N、O、P、Q、R、S、T、U、V、W、X、Y、Z、AA" in ai_messages[0]["content"]

    class _PromptSession:
        def __init__(self):
            self.payload = None

        def post(self, _url, **kwargs):
            self.payload = kwargs["json"]
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "choices": [{"message": {"content": '{"Answer": ["AA"]}'}}]
                },
            )

    provider = SiliconFlow()
    provider.api_endpoint = "http://answer.invalid/v1/chat/completions"
    provider.api_key = ""
    provider.model_name = "test-model"
    provider.min_interval = 0
    provider.timeout = 1
    provider.max_retries = 1
    provider.retry_delay = 0
    session = _PromptSession()
    provider._session = session

    assert provider._query(question) == "AA"
    assert "AA. item 26" in session.payload["messages"][1]["content"]
    assert "AA" in session.payload["messages"][0]["content"]


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
