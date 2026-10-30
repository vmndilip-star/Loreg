"""The demo path's safety nets: the non-streaming chat reply, the proxy header, the smoke script and the one-command reset. Offline."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.api.main import create_app
from auth import AuthedClient
from conftest import OfflineModel

REPO = Path(__file__).resolve().parent.parent
ASK = "What should I clear before the launch?"


@pytest.fixture
def shop(catalog_dir, tmp_path):
    client = AuthedClient(create_app(db_path=tmp_path / "t.sqlite", as_of="2026-10-26", llm=OfflineModel()))
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert client.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
    return client


def streamed(client, question):
    out = []
    for block in client.post("/api/ask", json={"question": question}).text.replace("\r\n", "\n").split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "event" in lines:
            out.append({"event": lines["event"], "data": json.loads(lines["data"])})
    return out


def strip_timing(events):
    return [{**e, "data": {k: v for k, v in e["data"].items() if k not in ("total_ms", "agents")}} for e in events]


def test_the_stream_is_never_buffered_by_a_proxy(shop):
    r = shop.post("/api/ask", json={"question": ASK})
    assert r.headers["content-type"].startswith("text/event-stream") and r.headers["x-accel-buffering"] == "no"


def test_stream_false_returns_the_same_events_at_once(shop):
    whole = shop.post("/api/ask?stream=false", json={"question": ASK})
    assert whole.status_code == 200 and whole.headers["content-type"].startswith("application/json")
    events = whole.json()["events"]
    assert events[0]["event"] == "status" and events[-1]["event"] == "done" and events[-1]["data"]["pipeline"] == "launch_clear"
    assert strip_timing(events) == strip_timing(streamed(shop, ASK))


def test_stream_false_still_needs_the_csrf_token(shop):
    token = shop.headers.pop("X-CSRF-Token")
    try:
        assert shop.post("/api/ask?stream=false", json={"question": ASK}).status_code == 403
    finally:
        shop.headers["X-CSRF-Token"] = token


def run_script(name, *args, env_extra=None):
    env = {**os.environ, "LLM_BACKEND": "mock", **(env_extra or {})}
    env.pop("META_API_KEY", None)
    return subprocess.run([sys.executable, str(REPO / "scripts" / name), *args], capture_output=True, text=True, env=env, cwd=REPO, timeout=300)


def test_demo_smoke_passes_on_the_mock_backend():
    r = run_script("demo_smoke.py", "--backend", "mock")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FAIL" not in r.stdout and "15 of 15 checks passed" in r.stdout


def test_reset_refuses_without_yes_and_then_gives_the_same_numbers_every_time(tmp_path):
    db = tmp_path / "reset.sqlite"
    env = {"LOREG_DB": str(db), "LOREG_AS_OF": ""}
    refused = run_script("reset_demo.py", env_extra=env)
    assert refused.returncode == 2 and not db.exists()
    first = run_script("reset_demo.py", "--yes", env_extra=env)
    assert first.returncode == 0, first.stdout + first.stderr
    second = run_script("reset_demo.py", "--yes", env_extra=env)
    assert second.returncode == 0 and "197 kept, 3 quarantined" in second.stdout and "stock date 2026-10-26" in second.stdout
    assert first.stdout.split("Demo shop loaded:")[1] == second.stdout.split("Demo shop loaded:")[1]
