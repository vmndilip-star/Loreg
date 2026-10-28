"""The demo shortcut: off by default (404, not 403), and when on it takes exactly the real upload path."""

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routes_demo import DEMO_FILE

ROOT = Path(__file__).resolve().parents[1]


def make(tmp_path, name="t.sqlite"):
    return TestClient(create_app(tmp_path / name, as_of="2026-10-26"))


def test_demo_routes_answer_404_when_the_flag_is_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("LOREG_DEMO", raising=False)
    c = make(tmp_path)
    assert c.post("/api/demo/load").status_code == 404
    assert c.get("/api/demo").status_code == 404
    assert c.get("/api/summary").status_code == 404                        # nothing was loaded


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_the_exact_value_1_turns_the_demo_on(tmp_path, monkeypatch, value):
    monkeypatch.setenv("LOREG_DEMO", value)
    assert make(tmp_path).post("/api/demo/load").status_code == 404


def test_demo_load_gives_the_same_result_as_uploading_the_same_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    demo, real = make(tmp_path, "a.sqlite"), make(tmp_path, "b.sqlite")
    assert demo.get("/api/demo").json() == {"enabled": True}
    loaded = demo.post("/api/demo/load").json()
    uploaded = real.post("/api/upload", files={"file": (DEMO_FILE.name, DEMO_FILE.read_bytes(), "text/csv")}).json()
    assert loaded == uploaded
    assert loaded["blocked"] == 3 and loaded["rows"] == 200        # the data checks ran: 3 seeded errors are quarantined
    assert demo.get("/api/summary").json() == real.get("/api/summary").json()
    assert demo.get(loaded["issues_url"]).json() == real.get(uploaded["issues_url"]).json()


def test_demo_load_replaces_stock_like_a_second_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    c = make(tmp_path)
    first = c.post("/api/demo/load").json()
    second = c.post("/api/demo/load").json()
    assert second["upload_id"] == first["upload_id"] + 1
    assert c.get("/api/summary").json()["totals"]["skus"] == first["kept"]


def test_the_bundled_demo_file_is_what_the_generator_makes(tmp_path):
    out = tmp_path / "gen"
    r = subprocess.run([sys.executable, str(ROOT / "data" / "generate_catalog.py"), "--seed", "2026", "--as-of", "2026-10-26", "--out", str(out)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-500:]
    norm = lambda b: b.replace(b"\r\n", b"\n")      # git may check the file out with CRLF on Windows
    assert norm((out / "upload_demo_200.csv").read_bytes()) == norm(DEMO_FILE.read_bytes())
