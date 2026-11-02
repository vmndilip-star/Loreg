"""The model client's error paths, through the real openai library and a fake HTTP transport: the one repair attempt after an answer
that does not validate, the 429 backoff with Retry-After, and an answer cut off at the token limit. No network, no real key."""

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from app.llm import client as llm_client
from app.llm.client import LLMClient, LLMError

FAKE_KEY = "test-key-not-a-real-one"


class Answer(BaseModel):
    word: str
    n: int


def completion(content: str, finish: str = "stop") -> dict:
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "fake",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


RATE_LIMITED = (429, {"error": {"message": "slow down", "type": "rate_limit", "code": "rate_limited"}})


class FakeApi:
    """Plays the Chat Completions endpoint from a list of (status, body[, headers]) and keeps every request body."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        status, body, *headers = self.replies.pop(0)
        return httpx.Response(status, json=body, headers=headers[0] if headers else None)


@pytest.fixture
def sleeps(monkeypatch):
    """The client's waits, recorded instead of slept; the jitter is set to zero so the delays can be checked exactly."""
    seen = []
    monkeypatch.setattr(llm_client, "time", SimpleNamespace(sleep=seen.append))
    monkeypatch.setattr(llm_client, "random", SimpleNamespace(uniform=lambda a, b: 0.0))
    return seen


def meta(monkeypatch, api: FakeApi) -> LLMClient:
    monkeypatch.setenv("META_API_KEY", FAKE_KEY)
    return LLMClient("meta", http_client=httpx.Client(transport=httpx.MockTransport(api.handler)))


def ask(client):
    return client.complete_json(system="s", user="u", schema=Answer)


def test_an_answer_that_does_not_validate_gets_one_repair_request(monkeypatch, sleeps):
    api = FakeApi((200, completion('{"word": "fine"}')), (200, completion('{"word": "fine", "n": 2}')))
    assert ask(meta(monkeypatch, api)) == Answer(word="fine", n=2)
    first, second = api.requests
    assert first["response_format"]["type"] == "json_schema" and first["response_format"]["json_schema"]["strict"] is True
    assert second["messages"][-2] == {"role": "assistant", "content": '{"word": "fine"}'}
    assert second["messages"][-1]["role"] == "user" and second["messages"][-1]["content"].startswith("That did not validate:")
    assert sleeps == []


def test_two_answers_that_do_not_validate_are_an_error_and_no_third_request_is_made(monkeypatch, sleeps):
    api = FakeApi((200, completion("not json")), (200, completion('{"n": "two"}')))
    with pytest.raises(LLMError, match="failed validation twice"):
        ask(meta(monkeypatch, api))
    assert len(api.requests) == 2


def test_a_429_waits_at_least_retry_after_then_succeeds(monkeypatch, sleeps):
    api = FakeApi((*RATE_LIMITED, {"retry-after": "3"}), (200, completion('{"word": "ok", "n": 1}')))
    assert ask(meta(monkeypatch, api)) == Answer(word="ok", n=1)
    assert sleeps == [3.0] and len(api.requests) == 2


def test_repeated_429s_back_off_doubling_and_give_up_after_five_attempts(monkeypatch, sleeps):
    api = FakeApi(*[RATE_LIMITED] * 5)
    with pytest.raises(LLMError, match="rate limited after 5 attempts") as err:
        ask(meta(monkeypatch, api))
    assert sleeps == [0.5, 1.0, 2.0, 4.0] and len(api.requests) == 5
    assert FAKE_KEY not in str(err.value)


def test_a_short_retry_after_never_shortens_the_backoff(monkeypatch, sleeps):
    api = FakeApi((*RATE_LIMITED, {"retry-after": "0"}), (*RATE_LIMITED, {"retry-after": "0"}), (200, completion('{"word": "ok", "n": 1}')))
    ask(meta(monkeypatch, api))
    assert sleeps == [0.5, 1.0]


def test_an_answer_cut_off_at_the_token_limit_is_an_error_not_a_repair(monkeypatch, sleeps):
    api = FakeApi((200, completion('{"word": "fi', finish="length")))
    with pytest.raises(LLMError, match="cut off"):
        ask(meta(monkeypatch, api))
    assert len(api.requests) == 1


def test_a_cut_off_text_answer_is_an_error_too(monkeypatch, sleeps):
    api = FakeApi((200, completion("partial", finish="length")))
    with pytest.raises(LLMError, match="cut off"):
        meta(monkeypatch, api).complete_text(system="s", user="u")


def test_another_http_error_is_one_attempt_and_the_key_is_scrubbed(monkeypatch, sleeps):
    api = FakeApi((500, {"error": {"message": f"bad key {FAKE_KEY}"}}))
    with pytest.raises(LLMError, match="HTTP 500") as err:
        ask(meta(monkeypatch, api))
    assert FAKE_KEY not in str(err.value) and len(api.requests) == 1 and sleeps == []
