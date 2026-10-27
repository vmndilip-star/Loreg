"""Line names must be identical across data/catalog_lines.yaml, the generated CSVs, the products table and the launch flag."""

import sqlite3

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.catalog import CATEGORY_LABEL, CATEGORY_ORDER, DEFAULT_LINES_PATH, load_catalog, norm, search_text, url_slug
from csv_truth import read_csv

SPEC = yaml.safe_load(DEFAULT_LINES_PATH.read_text(encoding="utf-8"))
YAML_LINES = {ln["id"]: dict(ln, category=cat) for cat in CATEGORY_ORDER for ln in SPEC["categories"][cat]["lines"]}


@pytest.fixture(scope="module")
def products(catalog_dir, tmp_path_factory):
    """The products table after uploading the full 3,000-product file."""
    db = tmp_path_factory.mktemp("names") / "t.sqlite"
    client = TestClient(create_app(db_path=db, as_of="2026-10-26"))
    with open(catalog_dir / "skus_master.csv", "rb") as f:
        assert client.post("/api/upload", files={"file": ("skus_master.csv", f, "text/csv")}).status_code == 200
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM products")]
    con.close()
    return rows


def test_line_ids_in_the_yaml_are_unique():
    ids = [ln["id"] for cat in CATEGORY_ORDER for ln in SPEC["categories"][cat]["lines"]]
    assert len(ids) == len(set(ids)) == 507


def test_every_product_maps_to_exactly_one_line(products):
    by_line = {}
    for p in products:
        by_line.setdefault(p["line_id"], []).append(p)
    assert set(by_line) == set(YAML_LINES) and len(products) == 3000          # all 507 lines, nothing else, nothing orphaned
    for line_id, ps in by_line.items():
        for col in ("line", "line_slug", "line_label", "year", "brand", "category", "launch_family"):
            assert len({p[col] for p in ps}) == 1, (line_id, col)             # one line_id never carries two line names or years


def test_line_names_match_the_yaml_and_the_launch_flag(products):
    for p in products:
        y = YAML_LINES[p["line_id"]]
        assert (p["line"], p["brand"], p["year"], p["category"]) == (y["line"], y["brand"], y["release_year"], y["category"]), p["sku"]
        assert bool(p["launch_family"]) == bool(y.get("launch_family")), p["sku"]
    assert sum(1 for p in products if p["launch_family"]) == 33                # the six Samsung 2024 TV lines


def test_the_generated_ground_truth_agrees_with_the_yaml(catalog_dir):
    for r in read_csv(catalog_dir / "ground_truth.csv"):
        y = YAML_LINES[r["line_id"]]
        assert r["category"] == y["category"] and (r["launch_family"] == "1") == bool(y.get("launch_family")), r["sku"]


def test_the_generated_names_are_the_catalog_names(catalog_dir):
    catalog = load_catalog()
    for r in read_csv(catalog_dir / "skus_master.csv"):
        v = catalog.match(r["brand"], r["model"])
        assert v is not None and v["sku"] == r["sku"] and v["category"] == r["category"], r["sku"]


def test_display_name_rule(products):
    years = {}
    for p in products:
        years.setdefault((p["category"], p["brand"], p["line"]), set()).add(p["year"])
    repeated = [k for k, y in years.items() if len(y) > 1]
    assert repeated and len(repeated) < len(years)                            # both cases exist in the real data
    for p in products:
        want = f"{p['line']} ({p['year']})" if len(years[(p["category"], p["brand"], p["line"])]) > 1 else p["line"]
        assert p["line_label"] == want, p["sku"]
        assert p["model"] == f"{p['line_label']} {p['variant']}", p["sku"]    # the model is the display name plus the variant


def test_line_slug_is_unique_per_category_and_matches_its_line(products):
    seen = {}
    for p in products:
        seen.setdefault((p["category"], p["line_slug"]), set()).add(p["line_id"])
    assert all(len(v) == 1 for v in seen.values())
    assert all(p["line_slug"] == url_slug(f"{p['brand']} {p['line_label']}") for p in products)
    assert all(p["line_slug"] == p["line_slug"].lower() and " " not in p["line_slug"] for p in products)


def test_search_text_is_normalized_and_complete(products):
    for p in products:
        assert p["search_text"] == norm(p["search_text"]) == p["search_text"].lower()
        for part in (p["brand"], p["model"], p["line"], p["variant"], p["sku"], CATEGORY_LABEL[p["category"]]):
            assert norm(part) in p["search_text"], (p["sku"], part)
        assert p["search_text"] == search_text(p["brand"], p["model"], p["line"], p["variant"], p["sku"], p["category"])


def test_variants_are_filled_for_every_product(products):
    assert all(p["variant"] and p["variant"] != "Unsorted" for p in products)
    assert {p["category"] for p in products} == set(CATEGORY_ORDER)


def test_catalog_matching_is_exact_not_fuzzy():
    catalog = load_catalog()
    v = catalog.variants[0]
    assert catalog.match(v["brand"], v["model"]) is v
    assert catalog.match(v["brand"].upper(), "  " + v["model"].upper() + " ") is v         # case and spacing only
    assert catalog.match(v["brand"], f"{v['brand']} {v['model']}") is v                    # brand typed in front of the model
    assert catalog.match(v["brand"], v["model"][:-1]) is None                              # one letter off: no match, no guess
    assert catalog.match("Nobody", v["model"]) is None and catalog.match("", "") is None
