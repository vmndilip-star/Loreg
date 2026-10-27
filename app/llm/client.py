"""The single door to a model. No other file in the repo calls a model API.

Backends, chosen by LLM_BACKEND (default "mock"):
  meta           Meta Model API, https://api.meta.ai/v1, key in META_API_KEY
  openai_compat  any OpenAI-compatible host (open weights), OPENAI_COMPAT_BASE_URL,
                 OPENAI_COMPAT_API_KEY, OPENAI_COMPAT_MODEL
  mock           no network; returns what the caller supplies (tests and offline demos)

Facts about Meta's Chat Completions API that this code relies on (dev.meta.ai/docs, and
confirmed by scripts/api_check.py): reasoning is always on and its tokens count toward
max_completion_tokens, so budgets are generous; tool_choice supports only "auto"; 429 means
back off with jitter and wait at least Retry-After.

The API key is read from the environment only. It is never logged, and error text is scrubbed.
"""

import json
import os
import random
import time
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

META_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_META_MODEL = "muse-spark-1.3"
DEFAULT_MAX_COMPLETION_TOKENS = 8000  # reasoning tokens count toward this
MAX_ATTEMPTS = 5
DEFAULT_TIMEOUT_S = 20.0
# Meta rejects reasoning_effort "none" (reasoning is always on). Lowest allowed value is "minimal".
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
# Measured on muse-spark-1.3 (see the latency table in the Phase report): "minimal" is 3 to 6 times faster than the default and every
# agent answer still passed the code-side checks (40 of 40 calls per setting). Used for the meta backend unless LLM_REASONING_EFFORT says otherwise.
DEFAULT_META_REASONING_EFFORT = "minimal"

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class LLMError(RuntimeError):
    """The model could not give a usable answer. Callers fall back to deterministic output."""


def strictify(schema):
    """Make a Pydantic JSON schema acceptable to strict mode: every object closed, every property required."""
    if isinstance(schema, dict):
        out = {k: strictify(v) for k, v in schema.items()}
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out
    if isinstance(schema, list):
        return [strictify(v) for v in schema]
    return schema


class _Chat:
    """OpenAI-compatible chat transport with 429 backoff."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str, reasoning_effort: str | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S):
        self.name, self.model, self._key = name, model, api_key
        self.reasoning_effort, self.timeout_s = reasoning_effort, timeout_s
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    def _scrub(self, text) -> str:
        return str(text).replace(self._key, "***") if self._key else str(text)

    def create(self, **kwargs):
        if self.reasoning_effort and "reasoning_effort" not in kwargs:
            kwargs["reasoning_effort"] = self.reasoning_effort
        kwargs.setdefault("timeout", self.timeout_s)  # per request; callers also bound the whole agent call
        delay = 0.5
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._client.chat.completions.create(model=self.model, **kwargs)
            except RateLimitError as e:
                if attempt == MAX_ATTEMPTS:
                    raise LLMError(f"{self.name}: rate limited after {attempt} attempts") from None
                retry_after = e.response.headers.get("retry-after")
                time.sleep(max(float(retry_after) if retry_after else 0.0, delay) + random.uniform(0, 0.25))
                delay *= 2
            except APIConnectionError as e:
                raise LLMError(f"{self.name}: connection failed: {self._scrub(e)[:200]}") from None
            except APIStatusError as e:
                raise LLMError(f"{self.name}: HTTP {e.status_code}: {self._scrub(e)[:300]}") from None


class LLMClient:
    def __init__(self, backend: str | None = None, reasoning_effort: str | None = None, timeout_s: float | None = None):
        load_dotenv(_ENV_FILE)
        self.backend = (backend or os.environ.get("LLM_BACKEND") or "mock").strip().lower()
        effort = (reasoning_effort or os.environ.get("LLM_REASONING_EFFORT") or "").strip().lower() or None
        if effort is None and self.backend == "meta":
            effort = DEFAULT_META_REASONING_EFFORT
        if effort is not None and effort not in REASONING_EFFORTS:
            raise LLMError(f"reasoning_effort {effort!r} is not allowed (Meta rejects 'none'); use one of {REASONING_EFFORTS}")
        timeout = float(timeout_s or os.environ.get("LLM_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        self.reasoning_effort, self.timeout_s = effort, timeout
        self._chat: _Chat | None = None
        if self.backend == "meta":
            key = os.environ.get("META_API_KEY", "")
            if not key:
                raise LLMError("LLM_BACKEND=meta but META_API_KEY is not set")
            self._chat = _Chat("meta", META_BASE_URL, key, os.environ.get("META_MODEL", DEFAULT_META_MODEL), effort, timeout)
        elif self.backend == "openai_compat":
            base, key, model = (os.environ.get(k, "") for k in
                                ("OPENAI_COMPAT_BASE_URL", "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_MODEL"))
            if not (base and model):
                raise LLMError("LLM_BACKEND=openai_compat needs OPENAI_COMPAT_BASE_URL and OPENAI_COMPAT_MODEL")
            self._chat = _Chat("openai_compat", base, key or "none", model, effort, timeout)
        elif self.backend != "mock":
            raise LLMError(f"unknown LLM_BACKEND {self.backend!r} (use meta, openai_compat or mock)")

    @property
    def is_mock(self) -> bool:
        return self._chat is None

    def complete_json(self, *, system: str, user: str, schema: type[T],
                      mock_output: Callable[[], T] | None = None,
                      max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS) -> T:
        """Ask for JSON matching `schema`; return it validated. Mock backend returns mock_output()."""
        if self._chat is None:
            if mock_output is None:
                raise LLMError("mock backend needs a mock_output for this call")
            return mock_output()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        response_format = {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "strict": True, "schema": strictify(schema.model_json_schema())}}
        last_problem = ""
        for _ in range(2):  # one repair attempt
            resp = self._chat.create(messages=messages, response_format=response_format,
                                     max_completion_tokens=max_completion_tokens)
            choice = resp.choices[0]
            if choice.finish_reason == "length":
                raise LLMError("answer was cut off: raise max_completion_tokens (reasoning tokens count)")
            try:
                return schema.model_validate_json(choice.message.content or "")
            except (ValidationError, json.JSONDecodeError) as e:
                last_problem = str(e)[:300]
                messages = messages + [
                    {"role": "assistant", "content": choice.message.content or ""},
                    {"role": "user", "content": f"That did not validate: {last_problem}. Return corrected JSON only."}]
        raise LLMError(f"model output failed validation twice: {last_problem}")

    def complete_text(self, *, system: str, user: str, mock_text: str = "",
                      max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS) -> str:
        if self._chat is None:
            return mock_text
        resp = self._chat.create(messages=[{"role": "system", "content": system},
                                           {"role": "user", "content": user}],
                                 max_completion_tokens=max_completion_tokens)
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMError("answer was cut off: raise max_completion_tokens (reasoning tokens count)")
        return choice.message.content or ""

    def stream_text(self, *, system: str, user: str, mock_text: str = "",
                    max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS) -> Iterator[str]:
        """Yield text pieces. Reasoning is not streamed, so pieces arrive in a few lumps."""
        if self._chat is None:
            for word in mock_text.split(" "):
                yield word + " "
            return
        stream = self._chat.create(messages=[{"role": "system", "content": system},
                                             {"role": "user", "content": user}],
                                   max_completion_tokens=max_completion_tokens, stream=True)
        for chunk in stream:
            for ch in chunk.choices:
                if ch.delta and ch.delta.content:
                    yield ch.delta.content
