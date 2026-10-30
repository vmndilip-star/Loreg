"""The eval set itself: at least 20 hand-worked cases covering the named traps, and every one passes on the mock backend. Offline."""

import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CASES = yaml.safe_load((REPO / "evals" / "cases.yaml").read_text(encoding="utf-8"))["cases"]


def test_the_set_has_at_least_twenty_cases_with_unique_ids():
    ids = [c["id"] for c in CASES]
    assert len(ids) >= 20 and len(set(ids)) == len(ids)


def test_the_set_covers_the_named_traps():
    ids = set(c["id"] for c in CASES)
    assert {"below-cost-trap", "return-window-expired", "map-caps-discount", "map-blocks-discount", "near-zero-data", "tiny-capital",
            "conflicting-signals"} <= ids


def test_every_case_passes_on_mock():
    env = {**os.environ, "LLM_BACKEND": "mock"}
    env.pop("META_API_KEY", None)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "run_evals.py"), "--backend", "mock"], capture_output=True, text=True,
                       env=env, cwd=REPO, timeout=300)
    assert r.returncode == 0 and f"{len(CASES)} of {len(CASES)} cases pass" in r.stdout, r.stdout + r.stderr
