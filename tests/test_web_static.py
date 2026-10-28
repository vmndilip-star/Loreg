"""Static checks on web/: safe DOM use, a strict Content-Security-Policy, nothing loaded from other origins."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import WEB_DIR, create_app

JS = sorted((WEB_DIR / "js").rglob("*.js"))
NODE = shutil.which("node")


def own_files():
    return [p for p in WEB_DIR.rglob("*") if p.is_file() and "vendor" not in p.parts and p.suffix in {".html", ".css", ".js"}]


def test_the_shell_is_served_with_a_content_security_policy(tmp_path):
    c = TestClient(create_app(tmp_path / "t.sqlite", as_of="2026-10-26"))
    html = c.get("/").text
    assert 'http-equiv="Content-Security-Policy"' in html
    policy = re.search(r'Content-Security-Policy" content="([^"]+)"', html).group(1)
    assert "script-src 'self'" in policy and "default-src 'self'" in policy and "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy
    assert c.get("/js/app.js").headers["content-type"].startswith("text/javascript")
    assert c.get("/api/health").json() == {"ok": True}      # the API still wins over the static mount
    assert c.get("/api/does-not-exist").status_code == 404


def test_no_unsafe_dom_or_code_execution_in_our_scripts():
    banned = ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout('", 'setTimeout("']
    for p in JS:
        code = " ".join(line.split("//")[0] for line in p.read_text(encoding="utf-8").splitlines())   # comments may name what is banned
        for b in banned:
            assert b not in code, f"{p.name} uses {b}"


def test_nothing_is_loaded_from_another_origin_and_no_inline_script_or_style():
    for p in own_files():
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"""(?:src|href)=["']https?://""", text), f"{p.name} loads a remote file"
        assert not re.search(r"url\(\s*['\"]?https?://", text), f"{p.name} loads a remote file from CSS"
        if p.suffix == ".html":
            assert not re.search(r"<script(?![^>]*\bsrc=)", text), "inline script"
            assert not re.search(r"\sstyle=", text), "inline style attribute"
            assert not re.search(r"\son[a-z]+=", text), "inline event handler"


def test_every_nav_link_has_a_view_module():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for name in re.findall(r'data-nav="(\w+)"', html):
        assert (WEB_DIR / "js" / "views" / f"{name}.js").is_file(), name


def test_money_is_never_computed_in_the_browser():
    """Cents arrive from the server and are only formatted. Views must not do arithmetic on *_cents fields."""
    for p in JS:
        if p.name == "format.js":      # the one place cents are turned into dollars, for display
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//")[0]
            assert not re.search(r"cents\s*[-+*/]\s*[\w(]|[\w)]\s*[-+*/]\s*\w*cents", code), f"{p.name}:{n} does arithmetic on cents: {line.strip()}"


@pytest.mark.skipif(NODE is None, reason="Node.js not found; skipping the JavaScript syntax and unit checks")
def test_scripts_parse_as_modules():
    for p in JS:
        r = subprocess.run([NODE, "--input-type=module", "--check"], input=p.read_text(encoding="utf-8"), capture_output=True, text=True)
        assert r.returncode == 0, f"{p.name}: {r.stderr}"


@pytest.mark.skipif(NODE is None, reason="Node.js not found; skipping the JavaScript syntax and unit checks")
def test_formatting_and_event_stream_reader_under_node():
    r = subprocess.run([NODE, str(Path(__file__).parent / "web" / "units.mjs")], capture_output=True, text=True)
    assert r.returncode == 0 and "web units ok" in r.stdout, r.stderr or r.stdout
