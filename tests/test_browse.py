"""Browse and search (Slice 1): /api/categories, /api/products, /api/search. Offline, no model (the no_model fixture fails the test if one is built).

Counts and money are compared with tests/csv_truth.py, an independent recomputation from the CSV (plain csv code, no app imports)."""

import ast
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.catalog import CATEGORY_LABEL, load_catalog
from csv_truth import Truth

pytestmark = pytest.mark.usefixtures("no_model")
AS_OF = "2026-10-26"
STATUSES = ["all", "dead", "slow", "healthy", "new"]


def upload(client, path):
    with open(path, "rb") as f:
        r = client.post("/api/upload", files={"file": (Path(path).name, f, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def demo(catalog_dir, tmp_path_factory):
    client = TestClient(create_app(tmp_path_factory.mktemp("browse") / "d.sqlite", as_of=AS_OF))
    upload(client, catalog_dir / "upload_demo_200.csv")
    return client


@pytest.fixture(scope="module")
def full(catalog_dir, tmp_path_factory):
    client = TestClient(create_app(tmp_path_factory.mktemp("browse_full") / "f.sqlite", as_of=AS_OF))
    upload(client, catalog_dir / "skus_master.csv")
    return client


@pytest.fixture(scope="module")
def truth(catalog_dir):
    return Truth(catalog_dir, master="upload_demo_200.csv")


@pytest.fixture(scope="module")
def truth_full(catalog_dir):
    return Truth(catalog_dir)


def subset(truth, category=None, status="all"):
    return [i for i in truth.items if (category is None or i["category"] == category) and (status == "all" or i["status"] == status)]


def at_risk(items):
    sel = [i for i in items if i["status"] in ("dead", "slow") and i["qty"] > 0]
    return len(sel), sum(i["cap"] for i in sel)


def get(client, url, **params):
    return client.get(url, params=params)


def all_rows(client, **params):
    out, page = [], 1
    while True:
        body = get(client, "/api/products", page=page, page_size=25, **params).json()
        out += body["rows"]
        if len(out) >= body["pagination"]["total"] or not body["rows"]:
            return out, body
        page += 1


# ------------------------------------------------------------------------------ categories
@pytest.mark.parametrize("which", ["demo", "full"])
def test_category_numbers_equal_the_csv_and_add_up_to_the_overall_totals(which, demo, full, truth, truth_full):
    client, t = (demo, truth) if which == "demo" else (full, truth_full)
    cats = get(client, "/api/categories").json()["categories"]
    for c in cats:
        mine = subset(t, c["key"])
        assert c["label"] == CATEGORY_LABEL[c["key"]] and c["items"] == len(mine)
        assert c["stock_cap_cents"] == sum(i["cap"] for i in mine)
        assert (c["dead_n"], c["slow_n"]) == (len(subset(t, c["key"], "dead")), len(subset(t, c["key"], "slow")))
        assert (c["at_risk_n"], c["at_risk_cap_cents"]) == at_risk(mine)
    assert [c["at_risk_cap_cents"] for c in cats] == sorted((c["at_risk_cap_cents"] for c in cats), reverse=True)     # biggest first
    s = get(client, "/api/summary").json()
    assert sum(c["items"] for c in cats) == s["totals"]["skus"] == len(t.items)
    assert sum(c["stock_cap_cents"] for c in cats) == s["totals"]["stock_at_cost_cents"]
    assert sum(c["dead_n"] for c in cats) == s["totals"]["dead"]["n"] and sum(c["slow_n"] for c in cats) == s["totals"]["slow"]["n"]
    assert sum(c["at_risk_n"] for c in cats) == s["totals"]["at_risk"]["n"]
    assert sum(c["at_risk_cap_cents"] for c in cats) == s["totals"]["at_risk"]["cap_cents"] == s["at_risk_all_cents"]


# ------------------------------------------------------------------------------ products
@pytest.mark.parametrize("category", [None, "tv", "phone", "laptop", "washer", "washer_dryer"])
@pytest.mark.parametrize("status", STATUSES)
def test_every_filter_equals_the_csv_and_paging_returns_each_row_exactly_once(demo, truth, category, status):
    want = subset(truth, category, status)
    rows, body = all_rows(demo, **({"category": category} if category else {}), status=status)
    assert body["totals"]["items"] == body["pagination"]["total"] == len(want) == len(rows)
    assert (body["totals"]["at_risk_n"], body["totals"]["at_risk_cap_cents"]) == at_risk(want)
    assert [r["sku"] for r in rows] == [i["sku"] for i in sorted(want, key=lambda i: (-i["cap"], i["sku"]))]      # money tied up, biggest first
    counts = body["totals"]["status_counts"]
    for s in STATUSES:
        assert counts[s] == len(subset(truth, category, s))                                                      # the chips' numbers


def test_a_row_carries_the_csv_values(demo, truth):
    by_sku = {i["sku"]: i for i in truth.items}
    rows, _ = all_rows(demo)
    assert len(rows) == len(truth.items)
    for r in rows:
        i = by_sku[r["sku"]]
        assert (r["name"], r["qty"], r["days_since_sold"], r["status"], r["tied_up_cents"], r["sell_price_cents"], r["category"]) == \
               (i["name"], i["qty"], i["dsls"], i["status"], i["cap"], i["sell"], i["category"])
        assert r["variant"] and r["line"] and r["line_slug"] and r["why"]


@pytest.mark.parametrize("sort", ["tied_up", "name", "qty", "days_since_sold"])
def test_each_sort_orders_by_the_csv_values(demo, truth, sort):
    rows, _ = all_rows(demo, sort=sort)
    by_sku = {i["sku"]: i for i in truth.items}
    keys = {"tied_up": lambda i: (-i["cap"], i["sku"]), "name": lambda i: (i["name"].lower(), i["sku"]), "qty": lambda i: (-i["qty"], i["sku"]),
            "days_since_sold": lambda i: (i["dsls"] is not None, -(i["dsls"] or 0), i["sku"])}[sort]        # never sold counts as the longest
    assert [r["sku"] for r in rows] == [i["sku"] for i in sorted(truth.items, key=keys)]
    assert [r["sku"] for r in get(demo, "/api/products").json()["rows"]] == [r["sku"] for r in all_rows(demo, sort="tied_up")[0]][:25]   # default sort is tied_up
    assert len(by_sku) == len(rows)


def test_pagination_edges(demo, truth):
    n = len(truth.items)
    last = (n + 24) // 25
    first = get(demo, "/api/products").json()
    assert len(first["rows"]) == 25 and first["pagination"] == {"page": 1, "page_size": 25, "total": n}
    final = get(demo, "/api/products", page=last).json()
    assert len(final["rows"]) == n - 25 * (last - 1)
    beyond = get(demo, "/api/products", page=last + 1)
    assert beyond.status_code == 200 and beyond.json()["rows"] == [] and beyond.json()["pagination"]["total"] == n
    assert len(get(demo, "/api/products", page_size=100).json()["rows"]) == 100
    assert len(get(demo, "/api/products", page_size=1).json()["rows"]) == 1


@pytest.mark.parametrize("params", [
    {"status": "bogus"}, {"status": "insufficient_data"}, {"status": "ALL"}, {"sort": "price"}, {"sort": "tied_up; DROP TABLE skus"},
    {"page": 0}, {"page": -1}, {"page": "x"}, {"page_size": 0}, {"page_size": 101}, {"page_size": "many"}, {"q": "x"}, {"q": "y" * 101},
])
def test_anything_outside_the_allowed_values_is_a_422(demo, params):
    assert get(demo, "/api/products", **params).status_code == 422


def test_an_unknown_category_is_a_404_not_an_error(demo):
    assert get(demo, "/api/products", category="gadgets").status_code == 404
    assert get(demo, "/api/products", category="tv' OR '1'='1").status_code == 404


# ------------------------------------------------------------------------------ search
def haystack(i):
    return f"{i['name']} {i['sku']} {CATEGORY_LABEL[i['category']]}".lower()


def test_an_exact_sku_comes_first_whatever_its_case(demo, truth):
    sku = truth.top(1)[0]["sku"]
    for q in (sku, sku.lower(), f"  {sku}  "):
        body = get(demo, "/api/search", q=q).json()
        assert body["items"][0]["sku"] == sku


def test_words_are_anded_case_does_not_matter_and_results_equal_the_csv(demo, truth):
    for q in ("samsung neo", "SAMSUNG NEO", "oled 55", "washer", "lg oled", "galaxy 256gb"):
        tokens = q.lower().split()
        want = sorted((i for i in truth.items if all(t in haystack(i) for t in tokens)), key=lambda i: (-i["cap"], i["sku"]))
        got = get(demo, "/api/search", q=q).json()["items"]
        assert {r["sku"] for r in got} <= {i["sku"] for i in want}
        assert len(got) == min(20, len(want)), q
        if len(want) <= 20:
            assert {r["sku"] for r in got} == {i["sku"] for i in want}, q


def test_a_name_prefix_ranks_above_a_contains_match(demo, truth):
    body = get(demo, "/api/search", q="samsung q").json()["items"]
    starts = [r["name"].lower().startswith("samsung q") for r in body]
    assert starts == sorted(starts, reverse=True) and any(starts)                      # every prefix match is listed before any other match


def test_lines_are_found_by_name_with_the_csv_money(demo, truth):
    body = get(demo, "/api/search", q="qn85d").json()
    assert 1 <= len(body["lines"]) <= 5 and body["lines"][0]["line"].startswith("QN85D")
    line = body["lines"][0]
    mine = [i for i in truth.items if i["name"].startswith("Samsung QN85D Neo QLED")]
    assert line["items"] == len(mine) and line["at_risk_cap_cents"] == at_risk(mine)[1] and line["stock_cap_cents"] == sum(i["cap"] for i in mine)
    assert len(get(demo, "/api/search", q="samsung").json()["lines"]) <= 5


def test_no_results_is_an_empty_normal_answer(demo):
    body = get(demo, "/api/search", q="zzzz qqqq")
    assert body.status_code == 200 and body.json()["items"] == [] and body.json()["lines"] == []


@pytest.mark.parametrize("q", ["%%", "__", "%sam", "tv_sam", "sam%", "a%b", "'", "''", '"', '";', "--", "\\", "50%", "tv\\_sam", "tv'sam"])
def test_percent_underscore_and_quotes_are_plain_text_never_wildcards(demo, truth, q):
    if len(q) < 2:
        assert get(demo, "/api/search", q=q).status_code == 422
        return
    body = get(demo, "/api/search", q=q)
    assert body.status_code == 200
    literal = [i for i in truth.items if q.lower() in haystack(i)]                    # only a real occurrence of the text can match
    assert len(body.json()["items"]) == min(20, len(literal)) == 0


@pytest.mark.parametrize("q", ["' OR '1'='1", "'; DROP TABLE skus; --", "\" OR 1=1 --", "x') UNION SELECT sku,sku,sku,sku,sku,sku,sku,sku,sku,sku,sku,sku FROM skus --",
                               "tv-sam-0040' AND 1=1", "1; DELETE FROM skus"])
def test_a_sql_injection_string_is_just_text_and_nothing_is_harmed(demo, q):
    con = sqlite3.connect(demo.app.state.db_path)
    before = con.execute("SELECT COUNT(*), SUM(capital_cents) FROM skus").fetchone()
    r = get(demo, "/api/search", q=q)
    p = get(demo, "/api/products", q=q)
    after = con.execute("SELECT COUNT(*), SUM(capital_cents) FROM skus").fetchone()
    con.close()
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["lines"] == []
    assert p.status_code == 200 and p.json()["rows"] == [] and p.json()["totals"]["items"] == 0
    assert before == after and before[0] > 0


def test_query_length_rules(demo):
    assert get(demo, "/api/search", q="x" * 101).status_code == 422
    assert get(demo, "/api/search", q="x" * 100).status_code == 200
    assert get(demo, "/api/search", q="a").status_code == 422 and get(demo, "/api/search", q="  a  ").status_code == 422
    assert get(demo, "/api/search", q="").status_code == 422 and get(demo, "/api/search", q="   ").status_code == 422
    assert get(demo, "/api/search", q="tv").status_code == 200                           # two characters is enough


def test_the_search_box_filter_on_products_matches_the_csv(demo, truth):
    want = [i for i in truth.items if all(t in haystack(i) for t in ("samsung", "neo"))]
    rows, body = all_rows(demo, q="samsung neo")
    assert len(rows) == len(want) == body["totals"]["items"] and body["filter"]["q"] == "samsung neo"
    assert (body["totals"]["at_risk_n"], body["totals"]["at_risk_cap_cents"]) == at_risk(want)
    rows, body = all_rows(demo, q="samsung neo", category="tv", status="slow")
    assert {r["sku"] for r in rows} == {i["sku"] for i in want if i["category"] == "tv" and i["status"] == "slow"}


# ------------------------------------------------------------------------------ data prerequisites
def test_product_rows_carry_the_catalog_names_the_launch_flag_and_a_search_text(demo):
    cat = load_catalog()
    con = sqlite3.connect(demo.app.state.db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM products").fetchall()
    assert rows
    for r in rows:
        hit = cat.match(r["brand"], r["model"])
        assert hit is not None and r["matched"] == 1, (r["brand"], r["model"])
        assert (r["line"], r["line_slug"], r["line_id"], r["line_label"], r["category"], r["variant"], int(r["launch_family"])) == \
               (hit["line"], hit["line_slug"], hit["line_id"], hit["line_label"], hit["category"], hit["variant"], int(hit["launch"]))
        text = r["search_text"]
        assert text == text.lower() and all(part.lower() in text for part in (r["brand"], r["model"], r["variant"], CATEGORY_LABEL[r["category"]]))
        assert r["sku"].lower() in text
    plan = " ".join(x[3] for x in con.execute("EXPLAIN QUERY PLAN SELECT * FROM products WHERE category = ?", ("tv",)))
    assert "idx_products_category" in plan
    plan = " ".join(x[3] for x in con.execute("EXPLAIN QUERY PLAN SELECT * FROM skus WHERE status = ?", ("dead",)))
    assert "idx_skus_status" in plan
    con.close()


def test_the_browse_code_never_imports_a_model_and_builds_no_query_from_caller_text():
    root = Path(__file__).resolve().parent.parent / "app"
    for path in (root / "api" / "routes_browse.py", root / "storage" / "browse.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(n == "openai" or n.startswith(("app.llm", "app.agents", "openai.")) for n in names), (path.name, names)
