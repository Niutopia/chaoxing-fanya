from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
import requests

import api.base as base
from api.answer import AI, CacheDAO, SiliconFlow, Tiku
from api.base import Account, Chaoxing, StudyResult, _resolve_choice_answer
from api.live_process import StudyCancelled


class _MemoryCache:
    def __init__(self):
        self.values = {}

    def get_cache(self, question):
        return self.values.get(question)

    def add_cache(self, question, answer):
        self.values[question] = answer

    _MISSING = object()

    def remove_cache(self, question, expected=_MISSING):
        if expected is not self._MISSING and self.values.get(question) != expected:
            return False
        self.values.pop(question, None)
        return True

    def replace_cache(self, question, answer):
        if answer is None:
            self.values.pop(question, None)
        else:
            self.values[question] = answer


class _StrictJudgementTiku(Tiku):
    """A non-AI provider whose judgement vocabulary is intentionally narrow."""

    def __init__(self, result):
        super().__init__()
        self.name = "strict synthetic provider"
        self.true_list = ["正确"]
        self.false_list = ["错误"]
        self.result = result
        self._cache = _MemoryCache()

    def _query(self, _question):
        return self.result


class _ChoiceClient:
    def __init__(self, contents, *, on_call=None):
        self.contents = list(contents)
        self.calls = []
        self.on_call = on_call

    def post(self, endpoint, *, headers, json):
        self.calls.append(
            {
                "endpoint": endpoint,
                "headers": dict(headers),
                "json": json,
            }
        )
        call_number = len(self.calls)
        if self.on_call is not None:
            self.on_call(call_number)
        content = self.contents.pop(0)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": content}}]
            },
        )


class _SiliconChoiceSession:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []

    def post(self, endpoint, *, headers, json, timeout):
        self.calls.append(
            {
                "endpoint": endpoint,
                "headers": dict(headers),
                "json": json,
                "timeout": timeout,
            }
        )
        content = self.contents.pop(0)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": content}}]
            },
        )


class _SiliconPlainResponse:
    """HTTP 200 response whose body is not JSON, as requests can return."""

    status_code = 200

    def __init__(self, text):
        self.text = text

    def json(self):
        raise ValueError("synthetic invalid JSON")


class _SiliconMixedResponseSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, endpoint, *, headers, json, timeout):
        self.calls.append(
            {
                "endpoint": endpoint,
                "headers": dict(headers),
                "json": json,
                "timeout": timeout,
            }
        )
        return self.responses.pop(0)


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


def _choice_question(*, question_id="q1"):
    return {
        "id": question_id,
        "title": "synthetic choice question",
        "options": "A. first\nB. second\nC. third",
        "type": "single",
        "answerField": {
            f"answer{question_id}": "",
            f"answertype{question_id}": "0",
        },
    }


def _ai_provider(client, *, cancel_event=None):
    provider = AI(request_semaphore=threading.BoundedSemaphore(1))
    provider._cache = _MemoryCache()
    provider.endpoint = "http://answer.invalid/v1/chat/completions"
    provider.key = "synthetic-key"
    provider.model = "synthetic-model"
    provider.min_interval_seconds = 0
    provider.timeout = 1
    provider.max_retries = 1
    provider.retry_delay = 0
    provider._httpx_client = client
    provider.set_cancel_event(cancel_event)
    return provider


def _silicon_provider(client, *, cache=None, cancel_event=None, max_retries=1):
    provider = SiliconFlow()
    provider._cache = cache or _MemoryCache()
    provider.api_endpoint = "http://answer.invalid/v1/chat/completions"
    provider.api_key = "synthetic-key"
    provider.model_name = "synthetic-model"
    provider.min_interval = 0
    provider.timeout = 1
    provider.max_retries = max_retries
    provider.retry_delay = 0
    provider._session = client
    provider._request_semaphore = threading.BoundedSemaphore(1)
    provider.set_cancel_event(cancel_event)
    return provider


def _run_ai_work(monkeypatch, provider, question):
    form = {"questions": [question], "answerwqbid": f"{question['id']},"}
    monkeypatch.setattr(base, "decode_questions_info", lambda *_a, **_k: form)
    session = _WorkSession()
    engine = Chaoxing(
        Account("synthetic-user", "synthetic-password"),
        tiku=provider,
        session=session,
    )
    engine.tiku.COVER_RATE = 0.0
    outcome = engine.study_work(
        {"courseId": "course", "clazzId": "clazz"},
        {"jobid": "work-1", "enc": "enc"},
        {"knowledgeid": "knowledge", "ktoken": "token", "cpi": "cpi"},
    )
    return outcome, session


def test_json_option_bodies_map_to_the_unique_page_labels():
    assert (
        _resolve_choice_answer(
            '{"Answer": ["second"]}',
            "A. first\nB. second\nC. third",
            multiple=False,
        )
        == "B"
    )
    assert (
        _resolve_choice_answer(
            {"answer": ["first", "third"]},
            "A. first\nB. second\nC. third",
            multiple=True,
        )
        == "AC"
    )


def test_complete_option_body_precedes_natural_language_label_tokens():
    assert (
        _resolve_choice_answer(
            "answer:B",
            "A. answer:B\nB. unrelated",
            multiple=False,
        )
        == "A"
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ("答案是 B。", "B"),
        ("正确选项为：C", "C"),
        ("The correct option is (B).", "B"),
        ("Correct answers: A and C", "AC"),
    ],
)
def test_natural_language_label_wrappers_are_mapped_strictly(result, expected):
    assert (
        _resolve_choice_answer(
            result,
            "A. first\nB. second\nC. third",
            multiple=len(expected) > 1,
        )
        == expected
    )


def test_natural_language_reads_only_positive_answer_fragment():
    assert (
        _resolve_choice_answer(
            "Correct answers: A and C. Option B is incorrect.",
            "A. first\nB. second\nC. third",
            multiple=True,
        )
        == "AC"
    )


def test_natural_language_accepts_explicit_positive_option_sentence():
    assert (
        _resolve_choice_answer(
            "Option B is correct.",
            "A. first\nB. second\nC. third",
            multiple=False,
        )
        == "B"
    )


def test_natural_language_repeated_labels_are_stably_deduplicated():
    assert (
        _resolve_choice_answer(
            "Correct answers: A, A",
            "A. first\nB. second",
            multiple=True,
        )
        == "A"
    )


def test_repeated_single_labels_are_stably_deduplicated():
    assert (
        _resolve_choice_answer(
            "A,A",
            "A. first\nB. second",
            multiple=False,
        )
        == "A"
    )


def test_choice_body_with_incidental_letters_is_not_treated_as_a_label():
    assert (
        _resolve_choice_answer(
            "Babbage machine",
            "A. Babbage machine\nB. Java",
            multiple=False,
        )
        == "A"
    )


def test_repeated_option_body_and_ambiguous_body_fail_closed():
    assert (
        _resolve_choice_answer(
            ["same", "same"],
            "A. same\nB. other",
            multiple=True,
        )
        == ""
    )
    assert (
        _resolve_choice_answer(
            "same",
            "A. same\nB. same\nC. other",
            multiple=False,
        )
        == ""
    )
    assert (
        _resolve_choice_answer(
            "答案是 E",
            "A. first\nB. second\nC. third\nD. fourth",
            multiple=False,
        )
        == ""
    )


def test_invalid_first_answer_gets_one_strict_repair_request_and_maps(monkeypatch):
    client = _ChoiceClient(
        [
            '{"Answer": ["not one of the options"]}',
            '{"Answer": ["B"]}',
        ]
    )
    provider = _ai_provider(client)
    question = _choice_question()

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 2
    assert question["answerSourceq1"] == "cover"
    assert session.posts[0]["answerq1"] == "B"
    repair_messages = client.calls[1]["json"]["messages"]
    assert "本题合法选项标签：A、B、C" in repair_messages[0]["content"]
    assert "A. first" in repair_messages[1]["content"]
    assert "B. second" in repair_messages[1]["content"]
    assert "仅返回JSON" in repair_messages[0]["content"]
    assert provider._cache.values[question["title"]] == "B"


def test_repair_accepts_only_pure_label_when_explanation_conflicts(monkeypatch):
    client = _ChoiceClient(
        [
            '{"Answer": ["not one of the options"]}',
            "Correct answer: B because this is explanatory text",
        ]
    )
    provider = _ai_provider(client)
    question = _choice_question()

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 2
    assert question["answerSourceq1"] == "uncovered"
    assert session.posts[0]["answerq1"] == ""
    assert question["title"] not in provider._cache.values


def test_repair_rejects_option_body_even_when_it_matches_uniquely(monkeypatch):
    client = _ChoiceClient(
        [
            '{"Answer": ["not one of the options"]}',
            "second",
        ]
    )
    provider = _ai_provider(client)
    question = _choice_question()

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert question["answerSourceq1"] == "uncovered"
    assert session.posts[0]["answerq1"] == ""


def test_failed_choice_cache_is_removed_so_next_work_queries_provider_again(
    monkeypatch,
):
    client = _ChoiceClient(
        [
            '{"Answer": ["not one of the options"]}',
            '{"Answer": ["A", "B"]}',
            '{"Answer": ["B"]}',
        ]
    )
    provider = _ai_provider(client)
    question = _choice_question()

    first_outcome, _ = _run_ai_work(monkeypatch, provider, question)
    assert first_outcome is StudyResult.SUCCESS
    assert question["title"] not in provider._cache.values

    second_outcome, second_session = _run_ai_work(monkeypatch, provider, question)

    assert second_outcome is StudyResult.SUCCESS
    assert len(client.calls) == 3
    assert "选择题答案格式修复" not in client.calls[2]["json"]["messages"][0]["content"]
    assert question["answerSourceq1"] == "cover"
    assert second_session.posts[0]["answerq1"] == "B"
    assert provider._cache.values[question["title"]] == "B"


def test_siliconflow_repair_makes_one_request_even_when_max_retries_is_zero(
    monkeypatch,
):
    cache = _MemoryCache()
    question = _choice_question()
    cache.values[question["title"]] = "not one of the options"
    client = _SiliconChoiceSession(['{"Answer": ["B"]}'])
    provider = _silicon_provider(
        client,
        cache=cache,
        max_retries=0,
    )

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 1
    assert question["answerSourceq1"] == "cover"
    assert session.posts[0]["answerq1"] == "B"
    assert cache.values[question["title"]] == "B"
    assert "仅返回JSON" in client.calls[0]["json"]["messages"][0]["content"]


def test_siliconflow_plain_text_choice_uses_one_initial_request_and_one_repair(
    monkeypatch,
):
    client = _SiliconChoiceSession(
        [
            "I think B because of the wording",
            '{"Answer": ["B"]}',
        ]
    )
    provider = _silicon_provider(client, max_retries=5)
    question = _choice_question()

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 2
    assert question["answerSourceq1"] == "cover"
    assert session.posts[0]["answerq1"] == "B"
    assert client.calls[0]["timeout"] == 1
    assert "选择题答案格式修复" in client.calls[1]["json"]["messages"][0]["content"]


def test_siliconflow_natural_language_label_is_mapped_without_format_retry(
    monkeypatch,
):
    client = _SiliconChoiceSession(["答案是 B。"])
    provider = _silicon_provider(client, max_retries=5)
    question = _choice_question()

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 1
    assert question["answerSourceq1"] == "cover"
    assert session.posts[0]["answerq1"] == "B"


def test_siliconflow_http_200_plain_text_body_maps_without_transport_retry(
    monkeypatch,
):
    client = _SiliconMixedResponseSession([_SiliconPlainResponse("答案是 B。")])
    provider = _silicon_provider(client, max_retries=5)
    question = _choice_question()

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 1
    assert question["answerSourceq1"] == "cover"
    assert session.posts[0]["answerq1"] == "B"


def test_siliconflow_http_200_malformed_text_gets_at_most_one_repair(
    monkeypatch,
):
    repaired = SimpleNamespace(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": '{"Answer": ["B"]}'}}]},
    )
    client = _SiliconMixedResponseSession(
        [_SiliconPlainResponse("not one of the options"), repaired]
    )
    provider = _silicon_provider(client, max_retries=5)
    question = _choice_question()

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 2
    assert question["answerSourceq1"] == "cover"
    assert session.posts[0]["answerq1"] == "B"
    assert "选择题答案格式修复" in client.calls[1]["json"]["messages"][0]["content"]


def test_non_ai_true_response_stays_unknown_and_save_only(monkeypatch):
    question = _choice_question()
    question.update(
        {
            "title": "synthetic strict judgement response",
            "options": "",
            "type": "judgement",
        }
    )
    question["answerField"]["answertypeq1"] = "1"
    provider = _StrictJudgementTiku("true")

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert question["answerSourceq1"] == "uncovered"
    assert session.posts[0]["pyFlag"] == "1"
    assert session.posts[0]["answerq1"] == ""
    assert question["title"] not in provider._cache.values


def test_non_ai_true_cache_hit_stays_unknown_and_save_only(monkeypatch):
    question = _choice_question()
    question.update(
        {
            "title": "synthetic strict judgement cache",
            "options": "",
            "type": "judgement",
        }
    )
    question["answerField"]["answertypeq1"] = "1"
    provider = _StrictJudgementTiku("正确")
    provider._cache.values[question["title"]] = "true"

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert question["answerSourceq1"] == "uncovered"
    assert session.posts[0]["pyFlag"] == "1"
    assert session.posts[0]["answerq1"] == ""
    assert provider._cache.values[question["title"]] == "true"


def test_siliconflow_timeout_retries_and_releases_request_slot():
    class _TimeoutSession:
        def __init__(self):
            self.calls = 0

        def post(self, *_args, **_kwargs):
            self.calls += 1
            raise requests.Timeout("synthetic timeout")

    client = _TimeoutSession()
    provider = _silicon_provider(client, max_retries=2)
    provider.retry_delay = 0

    assert provider._query(_choice_question()) is None
    assert client.calls == 2
    assert provider._request_semaphore.acquire(timeout=0.1)
    provider._request_semaphore.release()


def test_siliconflow_request_interval_waits_before_next_request(monkeypatch):
    client = _SiliconChoiceSession(['{"Answer": ["B"]}'])
    provider = _silicon_provider(client, max_retries=1)
    provider.min_interval = 10
    provider.last_request_time = time.time()
    waited = []
    monkeypatch.setattr(provider, "_wait_or_cancel", lambda seconds: waited.append(seconds))

    assert provider._query(_choice_question()) == "B"
    assert len(waited) == 1
    assert 0 < waited[0] <= 10


def test_siliconflow_cancellation_after_response_releases_request_slot():
    cancel_event = threading.Event()

    class _CancelAfterResponseSession:
        def __init__(self):
            self.calls = 0

        def post(self, *_args, **_kwargs):
            self.calls += 1
            cancel_event.set()
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"choices": [{"message": {"content": '{"Answer": ["B"]}'}}]},
            )

    client = _CancelAfterResponseSession()
    provider = _silicon_provider(client, cancel_event=cancel_event, max_retries=1)

    with pytest.raises(StudyCancelled):
        provider._query(_choice_question())

    assert client.calls == 1
    assert provider._request_semaphore.acquire(timeout=0.1)
    provider._request_semaphore.release()


def test_siliconflow_cancellation_while_waiting_for_request_slot_skips_post():
    cancel_event = threading.Event()

    class _CancellingSemaphore:
        def acquire(self, *, timeout):
            cancel_event.set()
            return False

    class _UnexpectedSession:
        def post(self, *_args, **_kwargs):
            raise AssertionError("cancelled request must not reach transport")

    provider = _silicon_provider(
        _UnexpectedSession(), cancel_event=cancel_event, max_retries=1
    )
    provider._request_semaphore = _CancellingSemaphore()

    with pytest.raises(StudyCancelled):
        provider._query(_choice_question())


def test_ai_repair_makes_one_request_even_when_max_retries_is_zero(monkeypatch):
    cache = _MemoryCache()
    question = _choice_question()
    cache.values[question["title"]] = "not one of the options"
    client = _ChoiceClient(['{"Answer": ["B"]}'])
    provider = _ai_provider(client)
    provider._cache = cache
    provider.max_retries = 0

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 1
    assert question["answerSourceq1"] == "cover"
    assert session.posts[0]["answerq1"] == "B"
    assert cache.values[question["title"]] == "B"


def test_ai_unknown_judgement_explanation_is_uncovered_without_randomization(
    monkeypatch,
):
    client = _ChoiceClient(['{"Answer": ["provider explanation"]}'])
    provider = _ai_provider(client)
    provider.true_list = ["正确"]
    provider.false_list = ["错误"]
    question = _choice_question()
    question.update(
        {
            "title": "synthetic judgement question",
            "options": "",
            "type": "judgement",
        }
    )
    question["answerField"]["answertypeq1"] = "1"
    question["answerField"]["answerq1"] = "old"
    monkeypatch.setattr(
        base.random,
        "choice",
        lambda *_args, **_kwargs: pytest.fail(
            "unknown judgement answers must not be randomized"
        ),
    )

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 1
    assert question["answerSourceq1"] == "uncovered"
    assert session.posts[0]["pyFlag"] == "1"
    assert session.posts[0]["answerq1"] == ""


def test_unknown_judgement_cache_is_cleared_and_second_run_queries_provider(
    monkeypatch,
):
    question = _choice_question()
    question.update(
        {
            "title": "synthetic judgement cache question",
            "options": "",
            "type": "judgement",
        }
    )
    question["answerField"]["answertypeq1"] = "1"
    cache = _MemoryCache()
    cache.values[question["title"]] = "provider explanation"
    client = _ChoiceClient(['{"Answer": ["正确"]}'])
    provider = _ai_provider(client)
    provider._cache = cache
    provider.true_list = ["正确"]
    provider.false_list = ["错误"]

    first_outcome, first_session = _run_ai_work(monkeypatch, provider, question)

    assert first_outcome is StudyResult.SUCCESS
    assert len(client.calls) == 0
    assert first_session.posts[0]["answerq1"] == ""
    assert question["title"] not in cache.values

    second_outcome, second_session = _run_ai_work(monkeypatch, provider, question)

    assert second_outcome is StudyResult.SUCCESS
    assert len(client.calls) == 1
    assert second_session.posts[0]["answerq1"] == "true"
    assert cache.values[question["title"]] == "true"

    third_outcome, third_session = _run_ai_work(monkeypatch, provider, question)

    assert third_outcome is StudyResult.SUCCESS
    assert len(client.calls) == 1
    assert third_session.posts[0]["answerq1"] == "true"


def test_partial_completion_is_not_cached_and_canonical_fill_hits_on_next_run(
    monkeypatch,
):
    question = {
        "id": "q1",
        "title": "synthetic completion cache question",
        "options": "",
        "type": "completion",
        "expectedBlankCount": 3,
        "answerField": {
            "answerq11": "",
            "answerq12": "",
            "answerq13": "",
            "answertypeq1": "2",
        },
    }
    client = _ChoiceClient(
        [
            '{"Answer": ["alpha", "", "gamma"]}',
            '{"Answer": ["alpha", "beta", "gamma"]}',
        ]
    )
    provider = _ai_provider(client)
    cache = provider._cache

    first_outcome, first_session = _run_ai_work(monkeypatch, provider, question)

    assert first_outcome is StudyResult.SUCCESS
    assert first_session.posts[0]["pyFlag"] == "1"
    assert len(client.calls) == 1
    assert question["title"] not in cache.values

    second_outcome, second_session = _run_ai_work(monkeypatch, provider, question)

    assert second_outcome is StudyResult.SUCCESS
    assert len(client.calls) == 2
    assert second_session.posts[0]["answerq11"] == "alpha"
    assert second_session.posts[0]["answerq12"] == "beta"
    assert second_session.posts[0]["answerq13"] == "gamma"
    assert cache.values[question["title"]] == "alpha\nbeta\ngamma"

    third_outcome, third_session = _run_ai_work(monkeypatch, provider, question)

    assert third_outcome is StudyResult.SUCCESS
    assert len(client.calls) == 2
    assert third_session.posts[0]["answerq11"] == "alpha"
    assert third_session.posts[0]["answerq12"] == "beta"
    assert third_session.posts[0]["answerq13"] == "gamma"


def test_partial_completion_cache_is_cleared_before_retrying_provider(
    monkeypatch,
):
    question = {
        "id": "q1",
        "title": "synthetic stale completion question",
        "options": "",
        "type": "completion",
        "expectedBlankCount": 3,
        "answerField": {
            "answerq11": "",
            "answerq12": "",
            "answerq13": "",
            "answertypeq1": "2",
        },
    }
    cache = _MemoryCache()
    cache.values[question["title"]] = "alpha\n\ngamma"
    client = _ChoiceClient(['{"Answer": ["alpha", "beta", "gamma"]}'])
    provider = _ai_provider(client)
    provider._cache = cache

    first_outcome, first_session = _run_ai_work(monkeypatch, provider, question)

    assert first_outcome is StudyResult.SUCCESS
    assert len(client.calls) == 0
    assert first_session.posts[0]["pyFlag"] == "1"
    assert question["title"] not in cache.values

    second_outcome, second_session = _run_ai_work(monkeypatch, provider, question)

    assert second_outcome is StudyResult.SUCCESS
    assert len(client.calls) == 1
    assert second_session.posts[0]["answerq12"] == "beta"
    assert cache.values[question["title"]] == "alpha\nbeta\ngamma"


def test_cache_remove_expected_is_compare_and_delete(tmp_path):
    cache = CacheDAO(str(tmp_path / "answers.json"))
    cache.add_cache("question", "bad")

    assert cache.remove_cache("question", expected="other") is False
    assert cache.get_cache("question") == "bad"
    assert cache.remove_cache("question", expected="bad") is True
    assert cache.get_cache("question") is None


@pytest.mark.parametrize("cache_kind", ["memory", "file"])
def test_stale_repair_cleanup_does_not_delete_concurrent_canonical(
    monkeypatch, tmp_path, cache_kind
):
    cache = (
        _MemoryCache()
        if cache_kind == "memory"
        else CacheDAO(str(tmp_path / "answers.json"))
    )
    question = _choice_question()
    cache.add_cache(question["title"], "bad cached answer")
    repair_started = threading.Event()
    allow_repair_finish = threading.Event()

    class _StaleRepairAI(AI):
        def repair_choice_answer(self, _question, _previous_answer=None):
            repair_started.set()
            assert allow_repair_finish.wait(2)
            return '{"Answer": ["still invalid"]}'

    provider = _StaleRepairAI()
    provider._cache = cache
    provider.true_list = ["正确"]
    provider.false_list = ["错误"]

    result = {}

    def run_work():
        result["value"] = _run_ai_work(monkeypatch, provider, question)

    worker = threading.Thread(target=run_work)
    worker.start()
    assert repair_started.wait(2)
    cache.replace_cache(question["title"], "B")
    allow_repair_finish.set()
    worker.join(timeout=3)
    assert not worker.is_alive()

    outcome, session = result["value"]
    assert outcome is StudyResult.SUCCESS
    assert question["answerSourceq1"] == "uncovered"
    assert session.posts[0]["pyFlag"] == "1"
    assert session.posts[0]["answerq1"] == ""
    assert cache.get_cache(question["title"]) == "B"


def test_legacy_cache_cleanup_skips_non_atomic_expected_fallback_during_race(
    monkeypatch,
):
    """A legacy cache must not get/read/remove around a concurrent write."""

    class _LegacyRaceCache:
        def __init__(self):
            self.values = {}
            self.read_count = 0
            self.cleanup_get = threading.Event()
            self.allow_cleanup = threading.Event()

        def get_cache(self, question):
            self.read_count += 1
            # The first read is Tiku.query's cache lookup and the second is
            # the base-layer observation.  A third read is the unsafe
            # get-then-remove fallback present in the old implementation.
            if self.read_count == 3:
                snapshot = self.values.get(question)
                self.cleanup_get.set()
                assert self.allow_cleanup.wait(2)
                return snapshot
            return self.values.get(question)

        def remove_cache(self, question):
            self.values.pop(question, None)

    question = _choice_question()
    cache = _LegacyRaceCache()
    cache.values[question["title"]] = "bad cached answer"

    class _StaleRepairAI(AI):
        def repair_choice_answer(self, _question, _previous_answer=None):
            return "still invalid"

    provider = _StaleRepairAI()
    provider._cache = cache
    provider.true_list = ["正确"]
    provider.false_list = ["错误"]

    result = {}

    def run_work():
        result["value"] = _run_ai_work(monkeypatch, provider, question)

    def write_canonical_between_get_and_remove():
        # On the old fallback this event is the barrier after its stale get
        # and before remove_cache(question).  On the fixed path no fallback
        # read occurs; the bounded wait still lets the test finish safely.
        cache.cleanup_get.wait(1)
        cache.values[question["title"]] = "B"
        cache.allow_cleanup.set()

    writer = threading.Thread(target=write_canonical_between_get_and_remove)
    worker = threading.Thread(target=run_work)
    writer.start()
    worker.start()
    worker.join(timeout=3)
    writer.join(timeout=3)

    assert not worker.is_alive()
    assert result["value"][0] is StudyResult.SUCCESS
    assert question["answerSourceq1"] == "uncovered"
    assert cache.values[question["title"]] == "B"


def test_legacy_add_only_cache_skips_expected_cleanup_without_overwrite(
    monkeypatch,
):
    class _LegacyAddOnlyCache:
        def __init__(self):
            self.values = {}

        def get_cache(self, question):
            return self.values.get(question)

        def add_cache(self, question, answer):
            self.values[question] = answer

    question = _choice_question()
    cache = _LegacyAddOnlyCache()
    cache.values[question["title"]] = "bad cached answer"

    class _StaleRepairAI(AI):
        def repair_choice_answer(self, _question, _previous_answer=None):
            return "still invalid"

    provider = _StaleRepairAI()
    provider._cache = cache
    provider.true_list = ["正确"]
    provider.false_list = ["错误"]

    outcome, _ = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert cache.values[question["title"]] == "bad cached answer"


def test_failed_repair_marks_uncovered_saves_blank_and_never_randomizes(monkeypatch):
    client = _ChoiceClient(
        [
            '{"Answer": ["not one of the options"]}',
            '{"Answer": ["A", "B"]}',
        ]
    )
    provider = _ai_provider(client)
    question = _choice_question()
    question["answerField"]["answerq1"] = "old"

    outcome, session = _run_ai_work(monkeypatch, provider, question)

    assert outcome is StudyResult.SUCCESS
    assert len(client.calls) == 2
    assert question["answerSourceq1"] == "uncovered"
    assert session.posts[0]["pyFlag"] == "1"
    assert session.posts[0]["answerq1"] == ""


def test_cancellation_before_repair_prevents_second_request_and_submission(
    monkeypatch,
):
    cancel_event = threading.Event()

    def cancel_after_first(call_number):
        if call_number == 1:
            cancel_event.set()

    client = _ChoiceClient(
        ['{"Answer": ["not one of the options"]}'],
        on_call=cancel_after_first,
    )
    provider = _ai_provider(client, cancel_event=cancel_event)

    with pytest.raises(StudyCancelled):
        _run_ai_work(monkeypatch, provider, _choice_question())

    assert len(client.calls) == 1


def test_cancellation_during_repair_aborts_without_submission(monkeypatch):
    cancel_event = threading.Event()

    def cancel_during_repair(call_number):
        if call_number == 2:
            cancel_event.set()

    client = _ChoiceClient(
        [
            '{"Answer": ["not one of the options"]}',
            '{"Answer": ["B"]}',
        ],
        on_call=cancel_during_repair,
    )
    provider = _ai_provider(client, cancel_event=cancel_event)
    question = _choice_question()

    with pytest.raises(StudyCancelled):
        _run_ai_work(monkeypatch, provider, question)

    assert len(client.calls) == 2
