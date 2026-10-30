"""Demo-day robustness: an overall request deadline with a visible fallback label, JSON logs with no secrets or prompts, a complete
.env.example and a boot with zero keys. Offline."""

import json
import logging
import re
import time
from pathlib import Path

import pytest

from app import obs
from app.api.main import create_app
from app.schemas.outputs import InventoryExplanation
from auth import AuthedClient
from conftest import OfflineModel

REPO = Path(__file__).resolve().parent.parent
ASK = "What should I clear before the launch?"


class Slow:
    """A real-looking backend that takes `seconds` for every inventory answer."""
    is_mock, backend = False, "meta"

    def __init__(self, seconds):
        self.seconds = seconds

    def complete_json(self, *, system, user, schema, mock_output=None, max_completion_tokens=0):
        if schema is InventoryExplanation:
            time.sleep(self.seconds)
        return mock_output()


def shop(catalog_dir, tmp_path, llm, **kw):
    c = AuthedClient(create_app(db_path=tmp_path / "t.sqlite", as_of="2026-10-26", llm=llm, **kw))
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert c.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
    return c


def test_the_whole_request_has_a_deadline_and_every_late_step_uses_the_rules(catalog_dir, tmp_path):
    c = shop(catalog_dir, tmp_path, Slow(3), agent_timeout_s=20, request_timeout_s=0.5)
    started = time.perf_counter()
    done = c.post("/api/ask?stream=false", json={"question": ASK}).json()["events"][-1]["data"]
    assert time.perf_counter() - started < 3                                    # not 20 s per step
    inventory = [a for a in done["agents"] if a["agent"] == "inventory"]
    assert inventory and all(a["outcome"] == "fallback" for a in inventory)


def test_the_page_labels_rules_based_fallback_on_answers_and_plan_cards():
    ask = (REPO / "web" / "js" / "views" / "ask.js").read_text(encoding="utf-8")
    plan = (REPO / "web" / "js" / "plan.js").read_text(encoding="utf-8")
    assert "Using rules-based fallback for" in ask and "a.outcome === 'fallback'" in ask
    assert "Using rules-based fallback: chosen by Loreg" in plan and "data-testid': 'plan-source'" in plan


def test_logs_are_json_with_a_request_id_and_carry_no_secrets_or_prompts(catalog_dir, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("META_API_KEY", "sk-test-secret-not-real")
    c = shop(catalog_dir, tmp_path, OfflineModel())
    obs.log.propagate = True                                                    # let caplog see the lines
    try:
        with caplog.at_level(logging.INFO, logger="loreg"):
            r = c.post("/api/ask?stream=false", json={"question": ASK})
            c.get("/api/summary", params={"secret": "query-should-not-be-logged"})
    finally:
        obs.log.propagate = False
    rid = r.headers["x-request-id"]
    lines = [json.loads(rec.getMessage()) for rec in caplog.records if rec.name == "loreg"]
    kinds = {line["kind"] for line in lines}
    assert {"request", "agent_call"} <= kinds
    calls = [line for line in lines if line["kind"] == "agent_call"]
    assert all(line["request_id"] == rid and line["backend"] and line["outcome"] in ("model_ok", "retried_ok", "fallback", "mock") for line in calls)
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    secrets = [v for v in c.cookies.values() if v] + [c.headers["X-CSRF-Token"]]
    assert secrets
    for leak in ("sk-test-secret-not-real", "query-should-not-be-logged", ASK, "FACTS", "Cookie", *secrets):
        assert leak not in text, leak


def env_names_read_by_the_app() -> set[str]:
    names = set()
    for path in (REPO / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r"environ\.get\(\s*\"([A-Z][A-Z0-9_]+)\"", text))
        names |= set(re.findall(r"\"((?:OPENAI_COMPAT|META|LLM|LOREG)_[A-Z0-9_]+)\"", text))
    return names


def test_env_example_names_every_variable_the_app_reads():
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    missing = sorted(n for n in env_names_read_by_the_app() if not re.search(rf"^#?\s*{n}=", example, re.M))
    assert not missing, missing


def test_the_app_boots_and_answers_on_mock_with_zero_keys(catalog_dir, tmp_path, monkeypatch):
    for name in env_names_read_by_the_app():
        monkeypatch.delenv(name, raising=False)
    c = shop(catalog_dir, tmp_path, None)                                       # the client is built from the environment: mock
    assert c.get("/api/health").json() == {"ok": True}
    assert c.get("/api/quality").json()["backend"] == "mock"
    done = c.post("/api/ask?stream=false", json={"question": ASK}).json()["events"][-1]["data"]
    assert done["backend"] == "mock" and done["agents"]


@pytest.mark.parametrize("backend", ["meta", "openai_compat"])
def test_the_real_backend_is_one_switch_and_says_what_is_missing(backend, monkeypatch):
    from app.llm.client import LLMClient, LLMError
    for name in env_names_read_by_the_app():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_BACKEND", backend)
    with pytest.raises(LLMError, match="META_API_KEY" if backend == "meta" else "OPENAI_COMPAT_BASE_URL"):
        LLMClient()
