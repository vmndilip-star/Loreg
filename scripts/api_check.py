"""One-off check of the Meta Model API (Chat Completions, OpenAI-compatible).

Runs four small tests with synthetic data only and prints, for each: pass/fail, latency,
finish_reason, token usage (including reasoning tokens) and the x-ratelimit headers.

    a) plain chat
    b) response_format json_schema, validated with Pydantic
    c) tool call (tool_choice "auto") with one fake read-only tool, including the tool result
    d) streaming, counting SSE chunks

Request formats come from https://dev.meta.ai/docs (protocols/chat-completions,
structured-output, tool-calling, pricing-rate-limits, reasoning). Notes from those pages:
  - base URL https://api.meta.ai/v1, model muse-spark-1.3, Bearer auth
  - reasoning always on; reasoning tokens count toward max_completion_tokens
  - tool_choice supports only "auto"
  - 429: back off with jitter from ~500 ms and wait at least Retry-After

The key is read from the META_API_KEY environment variable (or a local .env). It is never
printed; error text is scrubbed before it is shown.

Run from the repo root:  python scripts/api_check.py
"""

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from openai import APIStatusError, OpenAI, RateLimitError
from pydantic import BaseModel

BASE_URL = "https://api.meta.ai/v1"
MODEL = "muse-spark-1.3"
MAX_COMPLETION_TOKENS = 4000  # generous: reasoning tokens count toward this limit
MAX_ATTEMPTS = 5

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
API_KEY = os.environ.get("META_API_KEY", "")


def scrub(text):
    return str(text).replace(API_KEY, "***") if API_KEY else str(text)


def ratelimit_headers(headers):
    return {k.lower(): v for k, v in headers.items() if k.lower().startswith("x-ratelimit")}


def usage_line(usage):
    if usage is None:
        return "usage: not returned"
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) if details else None
    return (f"usage: prompt={usage.prompt_tokens} completion={usage.completion_tokens} "
            f"total={usage.total_tokens} reasoning={reasoning}")


def create(client, **kwargs):
    """chat.completions.create with 429 backoff. Returns (parsed_response, raw_headers)."""
    delay = 0.5
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = client.chat.completions.with_raw_response.create(model=MODEL, **kwargs)
            return raw.parse(), raw.headers
        except RateLimitError as e:
            if attempt == MAX_ATTEMPTS:
                raise
            retry_after = e.response.headers.get("retry-after")
            wait = max(float(retry_after) if retry_after else 0.0, delay) + random.uniform(0, 0.25)
            print(f"  429 received, waiting {wait:.2f}s (attempt {attempt}/{MAX_ATTEMPTS})")
            time.sleep(wait)
            delay *= 2


# ---- a) plain chat ---------------------------------------------------------------

def test_plain(client):
    t0 = time.time()
    resp, headers = create(
        client,
        messages=[{"role": "user", "content": "Reply with exactly one word: ready"}],
        max_completion_tokens=MAX_COMPLETION_TOKENS,
    )
    choice = resp.choices[0]
    ok = bool((choice.message.content or "").strip())
    return ok, time.time() - t0, choice.finish_reason, resp.usage, headers, \
        f"content={choice.message.content!r}"


# ---- b) structured output --------------------------------------------------------

class StockNote(BaseModel):
    sku: str
    status: Literal["dead", "slow", "healthy"]
    reason: str


def strict_schema(model):
    schema = model.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema["properties"])
    return schema


def test_structured(client):
    t0 = time.time()
    resp, headers = create(
        client,
        messages=[
            {"role": "system", "content": "Classify the item from the data given. Use only that data."},
            {"role": "user", "content": "SKU TV-001: qty 4, last sale 200 days ago, 0 sold in 90 days."},
        ],
        response_format={"type": "json_schema", "json_schema": {
            "name": "StockNote", "strict": True, "schema": strict_schema(StockNote)}},
        max_completion_tokens=MAX_COMPLETION_TOKENS,
    )
    choice = resp.choices[0]
    try:
        note = StockNote.model_validate_json(choice.message.content or "")
        ok, detail = True, f"validated: {note.model_dump()}"
    except Exception as e:  # noqa: BLE001 - report any parse/validation failure
        ok, detail = False, f"validation failed: {scrub(e)[:300]} | content={choice.message.content!r}"
    return ok, time.time() - t0, choice.finish_reason, resp.usage, headers, detail


# ---- c) tool call ----------------------------------------------------------------

TOOLS = [{"type": "function", "function": {
    "name": "get_days_of_stock",
    "description": "Read-only. Returns days of stock for one SKU, computed by the shop's code.",
    "parameters": {
        "type": "object",
        "properties": {"sku": {"type": "string", "description": "The SKU, for example TV-001"}},
        "required": ["sku"],
        "additionalProperties": False,
    },
}}]


def fake_get_days_of_stock(sku):
    return {"sku": sku, "days_of_stock": 142}  # synthetic


def test_tool(client):
    t0 = time.time()
    messages = [{"role": "user",
                 "content": "How many days of stock does SKU TV-001 have? Use the tool, then answer."}]
    resp, headers = create(client, messages=messages, tools=TOOLS, tool_choice="auto",
                           max_completion_tokens=MAX_COMPLETION_TOKENS)
    choice = resp.choices[0]
    first_finish = choice.finish_reason
    calls = choice.message.tool_calls or []
    if not calls:
        return False, time.time() - t0, first_finish, resp.usage, headers, \
            f"model did not call the tool (tool_choice auto); content={choice.message.content!r}"

    messages.append({"role": "assistant", "content": choice.message.content or "",
                     "tool_calls": [{"id": c.id, "type": "function",
                                     "function": {"name": c.function.name,
                                                  "arguments": c.function.arguments}} for c in calls]})
    for c in calls:
        args = json.loads(c.function.arguments)
        messages.append({"role": "tool", "tool_call_id": c.id,
                         "content": json.dumps(fake_get_days_of_stock(args["sku"]))})
    resp2, headers2 = create(client, messages=messages, tools=TOOLS, tool_choice="auto",
                             max_completion_tokens=MAX_COMPLETION_TOKENS)
    final = resp2.choices[0]
    ok = bool((final.message.content or "").strip()) and "142" in (final.message.content or "")
    total = None
    if resp.usage and resp2.usage:
        total = type("U", (), {})()
        total.prompt_tokens = resp.usage.prompt_tokens + resp2.usage.prompt_tokens
        total.completion_tokens = resp.usage.completion_tokens + resp2.usage.completion_tokens
        total.total_tokens = resp.usage.total_tokens + resp2.usage.total_tokens
        r1 = getattr(getattr(resp.usage, "completion_tokens_details", None), "reasoning_tokens", None)
        r2 = getattr(getattr(resp2.usage, "completion_tokens_details", None), "reasoning_tokens", None)
        total.completion_tokens_details = type("D", (), {"reasoning_tokens":
                                                          (r1 or 0) + (r2 or 0) if r1 is not None or r2 is not None else None})()
    return ok, time.time() - t0, f"{first_finish} -> {final.finish_reason}", total, headers2, \
        f"tool called with {calls[0].function.arguments}; final={final.message.content!r}"


# ---- d) streaming ----------------------------------------------------------------

def test_streaming(client):
    t0 = time.time()
    kwargs = dict(messages=[{"role": "user", "content": "Count from 1 to 5, separated by spaces."}],
                  max_completion_tokens=MAX_COMPLETION_TOKENS, stream=True)
    note = ""
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=MODEL, stream_options={"include_usage": True}, **kwargs)
        note = "stream_options include_usage accepted. "
    except APIStatusError as e:
        if e.status_code != 400:
            raise
        raw = client.chat.completions.with_raw_response.create(model=MODEL, **kwargs)
        note = "stream_options rejected (400); retried without. "
    stream = raw.parse()
    chunks, text, finish, usage = 0, [], None, None
    for chunk in stream:
        chunks += 1
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        for ch in chunk.choices:
            if ch.delta and ch.delta.content:
                text.append(ch.delta.content)
            if ch.finish_reason:
                finish = ch.finish_reason
    ok = chunks > 1 and bool("".join(text).strip())
    return ok, time.time() - t0, finish, usage, raw.headers, \
        f"{note}{chunks} SSE chunks; text={''.join(text).strip()!r}"


def run(name, fn, client):
    print(f"\n[{name}]")
    try:
        ok, latency, finish, usage, headers, detail = fn(client)
    except Exception as e:  # noqa: BLE001 - one failing test must not stop the others
        print(f"  FAIL  {type(e).__name__}: {scrub(e)[:400]}")
        return False
    print(f"  {'PASS' if ok else 'FAIL'}  latency={latency:.2f}s  finish_reason={finish}")
    print(f"  {usage_line(usage)}")
    print(f"  ratelimit: {json.dumps(ratelimit_headers(headers)) or '(none returned)'}")
    print(f"  {detail}")
    return ok


def main():
    if not API_KEY:
        print("META_API_KEY is not set (checked the environment and .env). Nothing was sent.")
        return 2
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, max_retries=0)  # we handle 429 ourselves
    print(f"Meta Model API check: {BASE_URL}  model={MODEL}  max_completion_tokens={MAX_COMPLETION_TOKENS}")
    results = [
        run("a) plain chat", test_plain, client),
        run("b) json_schema + Pydantic", test_structured, client),
        run("c) tool call + tool result", test_tool, client),
        run("d) streaming", test_streaming, client),
    ]
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
