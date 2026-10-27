"""The 3,000-product database: generate it once and run the full validator (including the determinism re-run)."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "data"))

import validate_catalog as V  # noqa: E402

GENERATOR = REPO / "data" / "generate_catalog.py"


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    out = tmp_path_factory.mktemp("catalog")
    subprocess.run([sys.executable, str(GENERATOR), "--seed", "2026", "--out", str(out)], check=True, capture_output=True)
    return out


def test_validator_passes_on_the_default_seed(database):
    checks = V.validate(database, 2026, "2026-10-26", None, determinism=True, quiet=True)
    failed = [(n, detail) for n, ok, detail in checks if not ok]
    assert not failed, failed
    assert len(checks) >= 50


def test_a_different_seed_gives_a_different_but_valid_database(database, tmp_path):
    subprocess.run([sys.executable, str(GENERATOR), "--seed", "7", "--out", str(tmp_path)], check=True, capture_output=True)
    assert (tmp_path / "skus_master.csv").read_bytes() != (database / "skus_master.csv").read_bytes()
    checks = V.validate(tmp_path, 7, "2026-10-26", None, determinism=False, quiet=True)
    assert all(ok for _, ok, _ in checks)


def test_unknown_anchor_ids_are_rejected(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("anchor_id,category,segment,price_type,usd,observed_on,evidence,source_url,note\n"
                   "tv_66_street,tv,66,street_floor,1,2026-10,estimated,,x\n")
    r = subprocess.run([sys.executable, str(GENERATOR), "--anchors", str(bad), "--out", str(tmp_path / "o")],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "unknown anchor_id" in (r.stderr + r.stdout)


def test_the_anchor_override_changes_prices_and_still_validates(tmp_path):
    ovr = tmp_path / "ovr.csv"
    ovr.write_text("anchor_id,category,segment,price_type,usd,observed_on,evidence,source_url,note\n"
                   "tv_65_street,tv,65,street_floor,339.99,2026-10,sourced_2026,https://example.com/x,test\n")
    out = tmp_path / "o"
    subprocess.run([sys.executable, str(GENERATOR), "--anchors", str(ovr), "--out", str(out)], check=True, capture_output=True)
    checks = V.validate(out, 2026, "2026-10-26", ovr, determinism=False, quiet=True)
    assert all(ok for _, ok, _ in checks)
