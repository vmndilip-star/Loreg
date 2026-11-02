"""Shared fixtures. All tests run offline with the mock model backend."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def offline_mock_backend(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "mock")
    monkeypatch.delenv("META_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def tests_never_read_a_local_env_file(monkeypatch):
    """A developer's .env (the README tells you to make one, and to switch the demo on in it) must not change what the tests check."""
    from app.llm import client
    monkeypatch.setattr(client, "load_dotenv", lambda *a, **k: False)
    for key in ("LOREG_DEMO", "LOREG_AS_OF", "LOREG_DB"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def stored_news_file_is_absent_unless_a_test_supplies_one(monkeypatch, tmp_path_factory):
    """data/demo/news_items.json (real items captured for the demo) would add stored copies to every demo-mode database. Tests that want it say so."""
    from app.api import routes
    monkeypatch.setattr(routes, "NEWS_ITEMS", tmp_path_factory.getbasetemp() / "no-such-news-items.json")
    monkeypatch.setattr(routes, "NEWS_ITEMS_LOCAL", tmp_path_factory.getbasetemp() / "no-such-news-items.local.json")


@pytest.fixture
def no_model(monkeypatch):
    """For the numbers API: any attempt to build a model client inside the test fails loudly."""
    from app.llm import client

    def refuse(self, *a, **k):
        raise AssertionError("a model client was created: this code path must not call a model")
    monkeypatch.setattr(client.LLMClient, "__init__", refuse)


@pytest.fixture(scope="session")
def catalog_dir(tmp_path_factory):
    """The 3,000-product database for the default seed, generated once per test session."""
    out = tmp_path_factory.mktemp("database")
    subprocess.run([sys.executable, str(REPO / "data" / "generate_catalog.py"), "--seed", "2026", "--out", str(out)],
                   check=True, capture_output=True)
    return out


class OfflineModel:
    """A model stand-in for tests of routes that may ask a model: it answers every request with the deterministic mock output.
    It is not an LLMClient, so it passes the no_model guard, which exists to catch numbers routes that build a real client."""

    is_mock = True

    def complete_json(self, *, system, user, schema, mock_output=None, max_completion_tokens=0):
        return mock_output()
