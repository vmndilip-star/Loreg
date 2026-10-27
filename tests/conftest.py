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
