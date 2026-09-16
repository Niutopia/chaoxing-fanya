"""Synthetic regressions for fill-in-the-blank decoding and submission."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import api.base as base
from api.answer import AI, SiliconFlow, TikuLike, question_cache_key
from api.base import Account, Chaoxing, StudyResult
from api.decode import decode_questions_info


class _CompletionTiku:
    DISABLE = False
    COVER_RATE = 0.8

    def __init__(self, result, *, submit=""):
        self.result = result
        self.submit = submit

    def query(self, _question):
        return self.result

    def get_submit_params(self):
        return self.submit

    def judgement_select(self, _answer):
        return True


class _NoopCache:
    def __init__(self, value=None):
        self.value = value
        self.added = []

    def get_cache(self, _question):
        return self.value

    def add_cache(self, question, answer):
        self.added.append((question, answer))


class _SubmissionSession:
    def __init__(self):
        self.posts = []

    def get(self, _url, **_kwargs):
        return SimpleNamespace(status_code=200, text="synthetic work html")

    def post(self, _url, **kwargs):
        self.posts.append(dict(kwargs.get("data") or {}))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": True, "msg": "saved"},
        )


def _completion_question(
    *,
    question_id="q1",
    fields=("answerq11", "answerq12", "answerq13"),
    expected=3,
):
    question = {
        "id": question_id,
        "title": "synthetic completion title",
        "options": "",
        "type": "completion",
        "answerField": {
            **{field: "" for field in fields},
            f"answertype{question_id}": "2",
        },
    }
    if expected is not None:
        question["expectedBlankCount"] = expected
    return question


def _run_completion(monkeypatch, question, result, *, cover_rate=0.8, submit=""):
    form = {"questions": [question], "answerwqbid": f"{question['id']},"}
    monkeypatch.setattr(base, "decode_questions_info", lambda *_a, **_k: form)
    session = _SubmissionSession()
    tiku = _CompletionTiku(result, submit=submit)
    tiku.COVER_RATE = cover_rate
    engine = Chaoxing(
        Account("synthetic-user", "synthetic-password"),
        tiku=tiku,
        session=session,
    )
    outcome = engine.study_work(
        {"courseId": "course", "clazzId": "clazz"},
        {"jobid": "work-1", "enc": "enc"},
        {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
    )
    return outcome, session.posts, question


def test_decoder_reads_primary_textarea_without_synthetic_aggregate():
    html = """
    <form>
      <input name="tiankongsizeq1" value="1">
      <div class="singleQuesId" data="q1">
        <div class="TiMu" data="2"><div class="Zy_TItle">synthetic title</div></div>
        <textarea name="answerq11">dom value</textarea>
      </div>
      <div class="singleQuesId" data="q11">
        <div class="TiMu" data="2"><div class="Zy_TItle">synthetic other</div></div>
        <textarea name="answerq111">other value</textarea>
      </div>
    </form>
    """

    result = decode_questions_info(html)

    q1, q11 = result["questions"]
    assert q1["answerField"]["answerq11"] == "dom value"
    assert "answerq1" not in q1["answerField"]
    assert q11["answerField"]["answerq111"] == "other value"
    assert "answerq11" not in q11["answerField"]
    assert result["tiankongsizeq1"] == "1"


def test_partial_completion_preserves_middle_and_tail_positions_and_saves(monkeypatch):
    question = _completion_question()

    outcome, posts, question = _run_completion(
        monkeypatch,
        question,
        ["alpha", "", "gamma"],
        cover_rate=0.0,
    )

    assert outcome is StudyResult.SKIPPED
    assert question["answerSourceq1"] == "partial"
    assert len(posts) == 1
    posted = posts[0]
    assert posted["pyFlag"] == "1"
    assert posted["answerq11"] == "alpha"
    assert posted["answerq12"] == ""
    assert posted["answerq13"] == "gamma"
    assert "answerq1" not in posted


def test_full_completion_can_submit_when_every_expected_blank_is_nonempty(monkeypatch):
    question = _completion_question()

    outcome, posts, question = _run_completion(
        monkeypatch,
        question,
        ["alpha", "beta", "gamma"],
        cover_rate=0.8,
    )

    assert outcome is StudyResult.SUCCESS
    assert question["answerSourceq1"] == "cover"
    assert posts[0]["pyFlag"] == ""
    assert posts[0]["answerq11"] == "alpha"
    assert posts[0]["answerq12"] == "beta"
    assert posts[0]["answerq13"] == "gamma"


def test_underscore_completion_fields_are_filled_without_cross_mode_fields(monkeypatch):
    question = _completion_question(
        fields=("answerq1_0", "answerq1_1"), expected=2
    )

    outcome, posts, _ = _run_completion(
        monkeypatch, question, ["alpha", "beta"], cover_rate=0.8
    )

    assert outcome is StudyResult.SUCCESS
    assert posts[0]["answerq1_0"] == "alpha"
    assert posts[0]["answerq1_1"] == "beta"
    assert "answerq11" not in posts[0]
    assert "answerq1" not in posts[0]


def test_legacy_aggregate_completion_remains_exact_and_is_not_indexed(monkeypatch):
    question = _completion_question(
        fields=("answerq1",), expected=None
    )

    outcome, posts, _ = _run_completion(
        monkeypatch, question, ["alpha", "beta"], cover_rate=0.8
    )

    assert outcome is StudyResult.SUCCESS
    assert posts[0]["answerq1"] == "alpha\nbeta"
    assert "answerq11" not in posts[0]
    assert "answerq12" not in posts[0]


@pytest.mark.parametrize("answer", ["alpha;beta", "left/right"])
def test_completion_text_punctuation_stays_in_one_blank(monkeypatch, answer):
    question = _completion_question(fields=("answerq11",), expected=1)

    outcome, posts, _ = _run_completion(
        monkeypatch, question, answer, cover_rate=0.8
    )

    assert outcome is StudyResult.SUCCESS
    assert posts[0]["answerq11"] == answer


def test_excess_completion_answers_fail_closed_without_post(monkeypatch):
    question = _completion_question(fields=("answerq11", "answerq12"), expected=2)

    outcome, posts, _ = _run_completion(
        monkeypatch, question, ["alpha", "beta", "gamma"], cover_rate=0.0
    )

    assert outcome is StudyResult.ERROR
    assert posts == []


def test_all_empty_completion_saves_and_clears_actual_fields(monkeypatch):
    question = _completion_question()

    outcome, posts, question = _run_completion(
        monkeypatch,
        question,
        ["", "", ""],
        cover_rate=0.9,
    )

    assert outcome is StudyResult.SKIPPED
    assert question["answerSourceq1"] == "uncovered"
    assert posts[0]["pyFlag"] == "1"
    assert posts[0]["answerq11"] == ""
    assert posts[0]["answerq12"] == ""
    assert posts[0]["answerq13"] == ""
    assert "answerq1" not in posts[0]


def test_partial_completion_forces_save_even_after_rollback(monkeypatch):
    question = _completion_question()
    form = {"questions": [question], "answerwqbid": "q1,"}
    monkeypatch.setattr(base, "decode_questions_info", lambda *_a, **_k: form)
    session = _SubmissionSession()
    tiku = _CompletionTiku(["alpha", "", "gamma"], submit="")
    tiku.COVER_RATE = 0.0
    engine = Chaoxing(
        Account("synthetic-user", "synthetic-password"),
        tiku=tiku,
        session=session,
    )
    engine.rollback_times = 1

    outcome = engine.study_work(
        {"courseId": "course", "clazzId": "clazz"},
        {"jobid": "work-1", "enc": "enc"},
        {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
    )

    assert outcome is StudyResult.SKIPPED
    assert session.posts[0]["pyFlag"] == "1"


class _CompletionHttpx:
    def __init__(self, content):
        self.content = content

    def post(self, _url, **_kwargs):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": self.content}}]},
        )


def test_ai_completion_normalization_keeps_empty_positions():
    provider = AI()
    provider.endpoint = "http://answer.invalid"
    provider.key = "synthetic-key"
    provider.model = "synthetic-model"
    provider.min_interval_seconds = 0
    provider.max_retries = 1
    provider.retry_delay = 0
    provider._httpx_client = _CompletionHttpx(
        '{"Answer": ["alpha", "", "gamma", ""]}'
    )

    assert provider._query({"type": "completion", "title": "synthetic"}) == (
        "alpha\n\ngamma\n"
    )


def test_siliconflow_completion_normalization_keeps_empty_positions():
    class _Session:
        def post(self, _url, **_kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "choices": [
                        {"message": {"content": '{"Answer": ["alpha", "", "gamma", ""]}'}},
                    ]
                },
            )

    provider = SiliconFlow()
    provider.api_endpoint = "http://answer.invalid"
    provider.api_key = "synthetic-key"
    provider.model_name = "synthetic-model"
    provider.min_interval = 0
    provider.max_retries = 1
    provider.retry_delay = 0
    provider.timeout = 1
    provider._session = _Session()

    assert provider._query({"type": "completion", "title": "synthetic"}) == (
        "alpha\n\ngamma\n"
    )


def test_tikulike_completion_normalization_keeps_empty_positions():
    provider = TikuLike()

    response = SimpleNamespace(
        json=lambda: {
            "code": 1,
            "results": {
                "output": {
                    "questionType": "FILL_IN_BLANK",
                    "answer": {"blanks": ["alpha", "", "gamma", ""]},
                }
            },
        }
    )

    assert provider._parse_response(response) == "alpha\n\ngamma\n"


def test_decoder_reads_question_level_blank_count_and_keeps_actual_aggregate():
    html = """
    <form>
      <div class="singleQuesId" data="q2">
        <div class="TiMu" data="2"><div class="Zy_TItle">aggregate</div></div>
        <input name="tiankongsizeq2" value="2">
        <textarea name="answerq2">existing aggregate</textarea>
      </div>
    </form>
    """

    question = decode_questions_info(html)["questions"][0]

    assert question["answerField"] == {
        "answertypeq2": "2",
        "answerq2": "existing aggregate",
    }
    assert question["expectedBlankCount"] == 2


def test_decoder_uses_legacy_aggregate_only_when_completion_has_no_controls():
    html = """
    <form>
      <input name="tiankongsizeq3" value="1">
      <div class="singleQuesId" data="q3">
        <div class="TiMu" data="2"><div class="Zy_TItle">fallback</div></div>
      </div>
    </form>
    """

    question = decode_questions_info(html)["questions"][0]

    assert question["answerField"] == {
        "answertypeq3": "2",
        "answerq3": "",
    }
    assert question["expectedBlankCount"] == 1


def test_one_based_completion_suffixes_are_sorted_numerically(monkeypatch):
    question = _completion_question(
        fields=("answerq110", "answerq12", "answerq11"), expected=3
    )

    outcome, posts, _ = _run_completion(
        monkeypatch, question, ["first", "second", "tenth"], cover_rate=0.8
    )

    assert outcome is StudyResult.SUCCESS
    assert posts[0]["answerq11"] == "first"
    assert posts[0]["answerq12"] == "second"
    assert posts[0]["answerq110"] == "tenth"


@pytest.mark.parametrize("provider_cls", [AI, SiliconFlow, TikuLike])
@pytest.mark.parametrize("cache_hit", [False, True])
def test_public_query_completion_keeps_trailing_blank_for_cache_and_query(
    provider_cls, cache_hit
):
    expected = "alpha\n\ngamma\n"
    provider = provider_cls()
    cache = _NoopCache(expected if cache_hit else None)
    provider._cache = cache
    calls = []

    def stub_query(_question):
        calls.append(True)
        return expected

    provider._query = stub_query
    question = {"title": "synthetic", "type": "completion"}

    assert provider.query(question) == expected
    assert calls == ([] if cache_hit else [True])


def test_public_query_non_completion_still_strips_provider_result():
    provider = TikuLike()
    provider._cache = _NoopCache(None)
    provider._query = lambda _question: "  alpha  \n"

    assert provider.query({"title": "synthetic", "type": "single"}) == "alpha"


@pytest.mark.parametrize("provider_cls", [AI, SiliconFlow])
@pytest.mark.parametrize(
    ("question_type", "result"),
    [
        ("single", "B"),
        ("multiple", "AC"),
        ("judgement", "provider explanation"),
        ("completion", "alpha\nbeta"),
    ],
)
def test_ai_queries_never_cache_raw_provider_results(
    provider_cls, question_type, result
):
    provider = provider_cls()
    cache = _NoopCache(None)
    provider._cache = cache
    provider._query = lambda _question: result

    assert provider.query({"title": "synthetic", "type": question_type}) == result
    assert cache.added == []


def test_non_ai_query_keeps_historical_raw_cache_write():
    provider = TikuLike()
    cache = _NoopCache(None)
    provider._cache = cache
    provider._query = lambda _question: "B"

    assert provider.query(
        {"title": "synthetic", "type": "single"}
    ) == "B"
    assert cache.added == [(question_cache_key({"title": "synthetic", "type": "single"}), "B")]


def test_ai_plain_text_completion_fallback_keeps_middle_and_tail_blank():
    provider = AI()
    provider.endpoint = "http://answer.invalid"
    provider.key = "synthetic-key"
    provider.model = "synthetic-model"
    provider.min_interval_seconds = 0
    provider.max_retries = 1
    provider.retry_delay = 0
    provider._httpx_client = _CompletionHttpx("alpha\r\n\r\ngamma\r\n")

    assert provider._query({"type": "completion", "title": "synthetic"}) == (
        "alpha\n\ngamma\n"
    )


def test_decoder_completion_count_uses_largest_index_mode_only():
    html = """
    <form>
      <div class="singleQuesId" data="q1">
        <div class="TiMu" data="2"><div class="Zy_TItle">dual mode</div></div>
        <input name="tiankongsizeq1" value="">
        <input name="answerq11" value="">
        <input name="answerq12" value="">
        <input name="answerq1_0" value="">
        <input name="answerq1_1" value="">
      </div>
    </form>
    """

    question = decode_questions_info(html)["questions"][0]

    assert question["expectedBlankCount"] == 2


@pytest.mark.parametrize(
    "fields", [("answerq11", "answerq12"), ("answerq1_0", "answerq1_1")]
)
def test_completion_submission_fills_both_index_modes_directly(monkeypatch, fields):
    question = _completion_question(fields=fields, expected=2)

    outcome, posts, _ = _run_completion(
        monkeypatch, question, ["alpha", "beta"], cover_rate=0.8
    )

    assert outcome is StudyResult.SUCCESS
    assert posts[0][fields[0]] == "alpha"
    assert posts[0][fields[1]] == "beta"
    assert posts[0]["pyFlag"] == ""


@pytest.mark.parametrize("result", [["alpha", "", "gamma", ""], "alpha\n\ngamma\n", ["", "", "", ""]])
def test_oversized_completion_result_fails_before_post(monkeypatch, result):
    question = _completion_question(fields=("answerq11", "answerq12", "answerq13"), expected=3)

    outcome, posts, _ = _run_completion(monkeypatch, question, result, cover_rate=0.0)

    assert outcome is StudyResult.ERROR
    assert posts == []


def test_expected_blank_count_smaller_than_fields_rejects_excess(monkeypatch):
    question = _completion_question(
        fields=("answerq11", "answerq12", "answerq13"), expected=2
    )

    outcome, posts, _ = _run_completion(
        monkeypatch, question, ["alpha", "beta", "gamma"], cover_rate=0.0
    )

    assert outcome is StudyResult.ERROR
    assert posts == []


def test_expected_blank_count_larger_than_fields_saves_partial(monkeypatch):
    question = _completion_question(fields=("answerq11", "answerq12"), expected=3)

    outcome, posts, question = _run_completion(
        monkeypatch, question, ["alpha", "beta"], cover_rate=0.8
    )

    assert outcome is StudyResult.SKIPPED
    assert question["answerSourceq1"] == "partial"
    assert posts[0]["pyFlag"] == "1"
    assert posts[0]["answerq11"] == "alpha"
    assert posts[0]["answerq12"] == "beta"


def test_all_empty_completion_with_rollback_still_saves(monkeypatch):
    question = _completion_question()
    form = {"questions": [question], "answerwqbid": "q1,"}
    monkeypatch.setattr(base, "decode_questions_info", lambda *_a, **_k: form)
    session = _SubmissionSession()
    tiku = _CompletionTiku(["", "", ""])
    tiku.COVER_RATE = 0.0
    engine = Chaoxing(
        Account("synthetic-user", "synthetic-password"), tiku=tiku, session=session
    )
    engine.rollback_times = 1

    outcome = engine.study_work(
        {"courseId": "course", "clazzId": "clazz"},
        {"jobid": "work-1", "enc": "enc"},
        {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
    )

    assert outcome is StudyResult.SKIPPED
    assert session.posts[0]["pyFlag"] == "1"


def test_decoder_q1_and_q11_answer_field_sets_are_symmetric():
    html = """
    <form>
      <div class="singleQuesId" data="q1">
        <div class="TiMu" data="2"><div class="Zy_TItle">one</div></div>
        <input name="answerq11" value="old-one">
        <input name="answerq12" value="old-two">
      </div>
      <div class="singleQuesId" data="q11">
        <div class="TiMu" data="2"><div class="Zy_TItle">eleven</div></div>
        <input name="answerq111" value="old-eleven-one">
        <input name="answerq112" value="old-eleven-two">
      </div>
    </form>
    """

    questions = decode_questions_info(html)["questions"]
    q1, q11 = questions

    assert set(q1["answerField"]) == {"answertypeq1", "answerq11", "answerq12"}
    assert set(q11["answerField"]) == {
        "answertypeq11",
        "answerq111",
        "answerq112",
    }


@pytest.mark.parametrize("fields", [("answerq11", "answerq12", "answerq13"), ("answerq1_0", "answerq1_1", "answerq1_2")])
def test_completion_mapping_clears_old_values_in_unfilled_positions(monkeypatch, fields):
    question = _completion_question(fields=fields, expected=3)
    for field in fields:
        question["answerField"][field] = "old"

    outcome, posts, _ = _run_completion(
        monkeypatch, question, ["alpha", "beta"], cover_rate=0.0
    )

    assert outcome is StudyResult.SKIPPED
    assert posts[0][fields[0]] == "alpha"
    assert posts[0][fields[1]] == "beta"
    assert posts[0][fields[2]] == ""


def test_ai_mapping_error_does_not_hide_later_future_error(monkeypatch):
    class _Future:
        def __init__(self, error):
            self.error = error

        def result(self):
            if self.error:
                raise self.error

    class _Executor:
        def __init__(self, **_kwargs):
            self.futures = [
                _Future(base._CompletionMappingError("too many")),
                _Future(RuntimeError("later worker failure")),
            ]
            self.index = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def submit(self, *_args, **_kwargs):
            future = self.futures[self.index]
            self.index += 1
            return future

    questions = {
        "questions": [
            _completion_question(question_id="q1"),
            _completion_question(question_id="q2"),
        ],
        "answerwqbid": "q1,q2,",
    }
    monkeypatch.setattr(base, "decode_questions_info", lambda *_a, **_k: questions)
    monkeypatch.setattr(base, "ThreadPoolExecutor", _Executor)
    session = _SubmissionSession()
    engine = Chaoxing(
        Account("synthetic-user", "synthetic-password"), tiku=AI(), session=session
    )

    with pytest.raises(RuntimeError, match="later worker failure"):
        engine.study_work(
            {"courseId": "course", "clazzId": "clazz"},
            {"jobid": "work-1", "enc": "enc"},
            {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
        )

    assert session.posts == []
