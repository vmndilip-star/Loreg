import pytest
from pydantic import BaseModel

from app.llm import client as client_module
from app.llm.client import LLMClient, LLMError, strictify
from app.schemas.outputs import MarketExtraction, Recommendation


class Box(BaseModel):
    n: int


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Tests must never pick up the real .env or its key."""
    monkeypatch.setattr(client_module, "load_dotenv", lambda *a, **k: None)
    for name in ("LLM_BACKEND", "META_API_KEY", "OPENAI_COMPAT_BASE_URL",
                 "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema:
            yield schema
        for v in schema.values():
            yield from _objects(v)
    elif isinstance(schema, list):
        for v in schema:
            yield from _objects(v)


@pytest.mark.parametrize("model", [Recommendation, MarketExtraction])
def test_strictify_closes_every_object_and_requires_every_property(model):
    schema = strictify(model.model_json_schema())
    found = list(_objects(schema))
    assert found
    for obj in found:
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])


def test_default_backend_is_mock():
    assert LLMClient().is_mock


def test_mock_returns_the_supplied_output():
    assert LLMClient("mock").complete_json(system="s", user="u", schema=Box, mock_output=lambda: Box(n=3)).n == 3


def test_mock_without_output_is_an_error():
    with pytest.raises(LLMError):
        LLMClient("mock").complete_json(system="s", user="u", schema=Box)


def test_mock_text_and_stream():
    c = LLMClient("mock")
    assert c.complete_text(system="s", user="u", mock_text="hello") == "hello"
    assert "".join(c.stream_text(system="s", user="u", mock_text="a b c")).split() == ["a", "b", "c"]


def test_meta_backend_needs_a_key():
    with pytest.raises(LLMError, match="META_API_KEY"):
        LLMClient("meta")


def test_openai_compat_needs_host_and_model():
    with pytest.raises(LLMError, match="OPENAI_COMPAT"):
        LLMClient("openai_compat")


def test_unknown_backend_is_an_error():
    with pytest.raises(LLMError, match="unknown"):
        LLMClient("nope")


def test_error_text_never_contains_the_key(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "sk-test-secret-123")
    c = LLMClient("meta")
    assert "sk-test-secret-123" not in c._chat._scrub("failed with key sk-test-secret-123 in text")
