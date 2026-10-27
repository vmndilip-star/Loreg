"""Numbers API tests. Offline (mock backend, and any model client creation fails the test).

Counts and money are compared with an independent recomputation from the CSV (tests/csv_truth.py: plain csv
code, nothing imported from app/core).
"""

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from csv_truth import CATEGORY_LABEL, Truth, read_csv

AS_OF = "2026-10-26"
pytestmark = pytest.mark.usefixtures("no_model")


def make_client(tmp_path, name="t.sqlite"):
    return TestClient(create_app(db_path=tmp_path / name, as_of=AS_OF))


def upload_file(client, path, **params):
    with open(path, "rb") as f:
        return client.post("/api/upload", files={"file": (Path(path).name, f, "text/csv")}, params=params)


@pytest.fixture(scope="module")
def full(catalog_dir, tmp_path_factory):
    """The whole 3,000-product database uploaded once; read-only tests share it."""
    client = make_client(tmp_path_factory.mktemp("full"))
    r = upload_file(client, catalog_dir / "skus_master.csv")
    assert r.status_code == 200, r.text
    return client, r.json()


@pytest.fixture(scope="module")
def truth(catalog_dir):
    return Truth(catalog_dir)


@pytest.fixture
def demo(catalog_dir, tmp_path):
    """A fresh app with the 200-row demo upload, for tests that change state."""
    client = make_client(tmp_path)
    r = upload_file(client, catalog_dir / "upload_demo_200.csv")
    assert r.status_code == 200, r.text
    return client, r.json(), tmp_path / "t.sqlite"


# ------------------------------------------------------------------------------ upload and data checks
def test_upload_of_the_full_database(full, catalog_dir):
    _, res = full
    master = read_csv(catalog_dir / "skus_master.csv")
    viral = sum(1 for r in master if int(r["units_sold_30d"]) > 30)  # the only rows the velocity check should warn about
    assert (res["rows"], res["kept"], res["blocked"], res["unsorted"]) == (3000, 3000, 0, 0)
    assert res["look"] == viral and viral >= 1
    assert res["zero_qty"] == sum(1 for r in master if r["qty_on_hand"] == "0")


def test_demo_upload_quarantines_exactly_three_rows_and_keeps_197(demo, catalog_dir):
    client, res, _ = demo
    assert (res["rows"], res["kept"], res["blocked"], res["look"], res["unsorted"]) == (200, 197, 3, 0, 0)
    issues = client.get(res["issues_url"]).json()
    assert issues["upload"]["ok"] == 197 and issues["upload"]["blocked"] == 3
    errors = {i["code"]: i["n"] for i in issues["issues"] if i["level"] == "error"}
    assert errors == {"MISSING_COST": 1, "NEGATIVE_QTY": 1, "DUPLICATE_SKU": 1}
    zero = sum(1 for r in read_csv(catalog_dir / "upload_demo_200.csv") if r["qty_on_hand"] == "0")
    assert zero >= 1 and issues["upload"]["zero_qty"] == zero                                  # info only, rows kept
    assert {i["code"] for i in issues["issues"]} - set(errors) == {"ZERO_QTY"}
    assert sum(1 for r in issues["rows"] if r["quarantined"]) == 3
    # the quarantined rows never reach the stock tables
    assert client.get("/api/summary").json()["totals"]["skus"] == 197


def test_demo_upload_numbers_equal_the_csv_recomputation(demo, catalog_dir):
    client, _, _ = demo
    t = Truth(catalog_dir, master="upload_demo_200.csv")
    assert len(t.items) == 197
    s = client.get("/api/summary").json()
    assert s["totals"] == t.totals()
    assert s["money_at_risk_cents"] == t.totals()["at_risk"]["cap_cents"]


def test_unknown_upload_id_is_404(full):
    client, _ = full
    assert client.get("/api/uploads/99999/issues").status_code == 404


@pytest.mark.parametrize("body, expected", [
    (b"", 422), (b"sku,brand,model\n", 422), (b"sku,brand,model,category,qty_on_hand,unit_cost,sell_price\n", 422),
    (b"\xff\xfe\x00bad", 422),
])
def test_unreadable_files_are_rejected(tmp_path, body, expected):
    client = make_client(tmp_path)
    assert client.post("/api/upload", content=body, headers={"content-type": "text/csv"}).status_code == expected


def test_a_file_over_5_mb_is_rejected(tmp_path):
    client = make_client(tmp_path)
    big = b"sku,brand,model,category,qty_on_hand,unit_cost,sell_price\n" + b"x" * (5 * 1024 * 1024 + 10)
    assert client.post("/api/upload", content=big, headers={"content-type": "text/csv"}).status_code == 413


def test_multipart_without_a_file_field_is_rejected(tmp_path):
    client = make_client(tmp_path)
    assert client.post("/api/upload", data={"other": "x"}, files={"nothere": ("a.csv", b"x", "text/csv")}).status_code == 422


def test_raw_csv_body_is_accepted(catalog_dir, tmp_path):
    client = make_client(tmp_path)
    r = client.post("/api/upload", content=(catalog_dir / "upload_demo_200.csv").read_bytes(), headers={"content-type": "text/csv"})
    assert r.status_code == 200 and r.json()["kept"] == 197


def test_a_new_upload_replaces_the_current_stock(catalog_dir, tmp_path):
    client = make_client(tmp_path)
    upload_file(client, catalog_dir / "upload_demo_200.csv")
    client.post("/api/alerts/1/help")
    r = upload_file(client, catalog_dir / "skus_master.csv")
    assert r.json()["upload_id"] == 2
    assert client.get("/api/summary").json()["totals"]["skus"] == 3000
    assert client.get("/api/alerts/1").status_code == 404  # the old alert is gone with the old stock, and its id is never reused
    assert client.get("/api/launch/exposed").json()["groups"][0]["help_requested"] is False


# ------------------------------------------------------------------------------ summary, top items, launch exposure
def test_summary_equals_the_csv_recomputation(full, truth):
    client, _ = full
    s = client.get("/api/summary").json()
    t = truth.totals()
    assert s["totals"] == t
    assert s["money_at_risk_cents"] == t["at_risk"]["cap_cents"] and s["accepted_cents"] == 0
    assert s["at_risk_all_cents"] == t["at_risk"]["cap_cents"]    # nothing accepted yet: the two numbers agree
    cats = truth.risk_by_category()
    assert [(c["key"], c["n"], c["cap_cents"]) for c in s["by_category"]] == cats
    assert [c["label"] for c in s["by_category"]] == [CATEGORY_LABEL[k] for k, _, _ in cats]
    assert s["by_category"][0]["pct"] == 100
    assert s["as_of"] == AS_OF and s["rule_version"] == 1


def test_category_totals_add_up_to_the_overall_totals(full):
    s = full[0].get("/api/summary").json()
    assert sum(c["n"] for c in s["by_category"]) == s["totals"]["at_risk"]["n"]
    assert sum(c["cap_cents"] for c in s["by_category"]) == s["totals"]["at_risk"]["cap_cents"]
    t = s["totals"]
    assert t["at_risk"]["n"] == t["dead"]["n"] + t["slow"]["n"]
    assert t["at_risk"]["cap_cents"] == t["dead"]["cap_cents"] + t["slow"]["cap_cents"]


def test_money_is_exact_integer_cents(full):
    s = full[0].get("/api/summary").json()
    assert all(isinstance(v, int) for v in (s["money_at_risk_cents"], s["totals"]["stock_at_cost_cents"], s["totals"]["dead"]["cap_cents"]))


@pytest.mark.parametrize("limit", [1, 8, 50])
def test_top_items_equal_the_csv_recomputation(full, truth, limit):
    body = full[0].get("/api/inventory/top", params={"limit": limit}).json()
    want = truth.top(limit)
    assert [i["sku"] for i in body["items"]] == [w["sku"] for w in want]
    assert [i["cap_cents"] for i in body["items"]] == [w["cap"] for w in want]
    assert [i["status"] for i in body["items"]] == [w["status"] for w in want]
    assert [i["dsls"] for i in body["items"]] == [w["dsls"] for w in want]
    assert [i["name"] for i in body["items"]] == [w["name"] for w in want]
    caps = [i["cap_cents"] for i in body["items"]]
    assert caps == sorted(caps, reverse=True)


def test_top_items_explain_themselves(full):
    for i in full[0].get("/api/inventory/top", params={"limit": 20}).json()["items"]:
        assert i["why"] and (i["why"].startswith("No sale in") or i["why"] == "Too much stock for its sales pace")


@pytest.mark.parametrize("limit", [0, 51, -1])
def test_top_items_limit_is_bounded(full, limit):
    assert full[0].get("/api/inventory/top", params={"limit": limit}).status_code == 422


def test_launch_exposed_equals_the_csv_recomputation(full, truth):
    groups = full[0].get("/api/launch/exposed").json()["groups"]
    want = truth.launch_exposed()
    assert len(groups) == 1 and want
    g = groups[0]
    assert g["brand"] == "Samsung" and g["category"] == "tv" and g["label"] == "Samsung TVs"
    assert g["n"] == len(want) and g["cap_cents"] == sum(w["cap"] for w in want)
    assert [t["sku"] for t in g["top"]] == [w["sku"] for w in want[:3]]
    assert g["rest_n"] == len(want) - 3 and g["rest_cap_cents"] == sum(w["cap"] for w in want[3:])
    assert g["help_requested"] is False and "no launch date" in g["source_note"].lower()


def test_launch_exposed_shows_no_options_before_help(full):
    text = full[0].get("/api/launch/exposed").text.lower()
    assert "option" not in text and "recommendation" not in text and "discount" not in text


def test_nothing_is_served_before_an_upload(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/summary").status_code == 404
    assert client.get("/api/inventory/top").json()["items"] == []
    assert client.get("/api/launch/exposed").json()["groups"] == []
    assert client.get("/api/needs-look").json() == {"count": 0, "unsorted": 0, "items": []}


# ------------------------------------------------------------------------------ alert-first, plans, decisions
def first_alert(client):
    return client.get("/api/launch/exposed").json()["groups"][0]["alert_id"]


def test_plan_without_help_is_refused(demo):
    client, _, _ = demo
    a = first_alert(client)
    r = client.post(f"/api/alerts/{a}/plan")
    assert r.status_code == 409 and "ask for help" in r.json()["detail"]
    assert client.get(f"/api/alerts/{a}").json()["recommendations"] == {}  # nothing was created by the refusal


def test_help_is_recorded_once_and_changes_nothing_else(demo):
    client, _, _ = demo
    a = first_alert(client)
    before = client.get("/api/summary").json()["money_at_risk_cents"]
    s1 = client.post(f"/api/alerts/{a}/help").json()
    s2 = client.post(f"/api/alerts/{a}/help").json()
    assert s1["help_requested"] and s1["help_requested_at"] == s2["help_requested_at"]
    assert client.get("/api/summary").json()["money_at_risk_cents"] == before


def test_plan_after_help_gives_one_option_per_item(demo):
    client, _, _ = demo
    a = first_alert(client)
    client.post(f"/api/alerts/{a}/help")
    body = client.post(f"/api/alerts/{a}/plan").json()
    recs = body["recommendations"]
    assert 1 <= len(recs) <= 3 and len({r["sku"] for r in recs}) == len(recs)
    for r in recs:
        assert r["option"] in ("discount", "bundle", "return", "hold") and r["status"] == "proposed"
        assert (r["option"] == "discount") == (r["discount_pct"] > 0) and len(r["reasons"]) >= 1 and r["citations"]
    caps = [r["tied_cents"] for r in recs]
    assert caps == sorted(caps, reverse=True)
    again = client.post(f"/api/alerts/{a}/plan").json()["recommendations"]
    assert [r["id"] for r in again] == [r["id"] for r in recs]  # planning twice does not duplicate


@pytest.mark.parametrize("limit", [0, 11])
def test_plan_limit_is_bounded(demo, limit):
    client, _, _ = demo
    a = first_alert(client)
    client.post(f"/api/alerts/{a}/help")
    assert client.post(f"/api/alerts/{a}/plan", params={"limit": limit}).status_code == 422


def planned(client):
    a = first_alert(client)
    client.post(f"/api/alerts/{a}/help")
    return client.post(f"/api/alerts/{a}/plan").json()["recommendations"]


def test_accepting_lowers_money_at_risk_by_exactly_that_items_capital(demo):
    client, _, _ = demo
    recs = planned(client)
    before = client.get("/api/summary").json()["money_at_risk_cents"]
    r = client.post(f"/api/actions/{recs[0]['id']}/accept").json()
    assert r["status"] == "accepted" and r["capital_cents"] == recs[0]["tied_cents"]
    assert r["money_at_risk_cents"] == before - recs[0]["tied_cents"]
    s = client.get("/api/summary").json()
    assert s["money_at_risk_cents"] == before - recs[0]["tied_cents"] and s["accepted_cents"] == recs[0]["tied_cents"]
    assert s["totals"]["at_risk"]["cap_cents"] == before  # the status totals themselves do not change
    assert s["at_risk_all_cents"] == before and s["money_at_risk_cents"] < s["at_risk_all_cents"]  # the page can label them separately
    r2 = client.post(f"/api/actions/{recs[1]['id']}/accept").json()
    assert r2["money_at_risk_cents"] == before - recs[0]["tied_cents"] - recs[1]["tied_cents"]


def test_accepted_capital_matches_the_csv_for_that_sku(demo, catalog_dir):
    client, _, _ = demo
    recs = planned(client)
    truth = {i["sku"]: i for i in Truth(catalog_dir, master="upload_demo_200.csv").items}
    for r in recs:
        assert r["tied_cents"] == truth[r["sku"]]["cap"] and r["shelf"] == truth[r["sku"]]["qty"]


def test_declining_leaves_money_at_risk_unchanged(demo):
    client, _, _ = demo
    recs = planned(client)
    before = client.get("/api/summary").json()["money_at_risk_cents"]
    r = client.post(f"/api/actions/{recs[0]['id']}/decline").json()
    assert r["status"] == "rejected" and r["money_at_risk_cents"] == before and r["accepted_cents"] == 0


def test_decisions_are_idempotent_and_final(demo):
    client, _, _ = demo
    recs = planned(client)
    a, b = recs[0]["id"], recs[1]["id"]
    first = client.post(f"/api/actions/{a}/accept").json()
    assert client.post(f"/api/actions/{a}/accept").json()["money_at_risk_cents"] == first["money_at_risk_cents"]  # not subtracted twice
    assert client.post(f"/api/actions/{a}/decline").status_code == 409
    client.post(f"/api/actions/{b}/decline")
    assert client.post(f"/api/actions/{b}/accept").status_code == 409


def test_nothing_is_accepted_without_a_recorded_help_request(demo):
    client, _, db = demo
    recs = planned(client)
    con = sqlite3.connect(db)  # white box: pretend the help request was never recorded
    con.execute("UPDATE alerts SET help_requested_at = NULL")
    con.commit()
    con.close()
    before = client.get("/api/summary").json()["money_at_risk_cents"]
    for verb in ("accept", "decline"):
        r = client.post(f"/api/actions/{recs[0]['id']}/{verb}")
        assert r.status_code == 409
    assert client.get("/api/summary").json()["money_at_risk_cents"] == before
    assert client.post(f"/api/alerts/{first_alert(client)}/plan").status_code == 409


def test_unknown_alert_and_action_ids_are_404(demo):
    client, _, _ = demo
    for url in ("/api/alerts/9999", ):
        assert client.get(url).status_code == 404
    for url in ("/api/alerts/9999/help", "/api/alerts/9999/plan", "/api/actions/9999/accept", "/api/actions/9999/decline"):
        assert client.post(url).status_code == 404


def test_decisions_are_written_to_the_audit_log(demo):
    client, _, db = demo
    recs = planned(client)
    client.post(f"/api/actions/{recs[0]['id']}/accept")
    con = sqlite3.connect(db)
    actions = [r[0] for r in con.execute("SELECT action FROM audit_log ORDER BY id")]
    con.close()
    assert actions[:3] == ["upload", "help_requested", "plan_proposed"] and actions[-1] == "accepted"


# ------------------------------------------------------------------------------ Unsorted, needs-a-look
def test_rows_that_match_no_catalog_line_go_to_unsorted_and_need_a_look(catalog_dir, tmp_path):
    master = read_csv(catalog_dir / "skus_master.csv")
    good = next(r for r in master if r["category"] == "tv")
    unknown = dict(good, sku="X-1", model="Galaxy Mystery 5G 128GB Pink", category="phone")
    near_miss = dict(good, sku="X-2", model=good["model"].replace(" inch", " inc"))     # a typo of a real model: still no guess
    header = list(master[0].keys())
    lines = [",".join(header)] + [",".join(r[h] if "," not in r[h] else f'"{r[h]}"' for h in header) for r in (good, unknown, near_miss)]
    client = make_client(tmp_path)
    res = client.post("/api/upload", content="\n".join(lines).encode(), headers={"content-type": "text/csv"}).json()
    assert (res["rows"], res["kept"], res["unsorted"], res["look"], res["blocked"]) == (3, 3, 2, 2, 0)
    nl = client.get("/api/needs-look").json()
    assert nl["count"] == 2 and nl["unsorted"] == 2
    assert {i["sku"]: i["line"] for i in nl["items"]} == {"X-1": "Unsorted", "X-2": "Unsorted"}
    assert all("UNSORTED_LINE" in i["reasons"] for i in nl["items"])
    con = sqlite3.connect(tmp_path / "t.sqlite")
    rows = {r[0]: r[1:] for r in con.execute("SELECT sku, line, line_id, line_slug, year, matched FROM products")}
    con.close()
    assert rows["X-1"] == ("Unsorted", "unsorted", "unsorted", None, 0) and rows[good["sku"]][4] == 1
    assert client.get(res["issues_url"]).json()["upload"]["ok"] == 1


def test_no_unsorted_rows_in_either_generated_file(full, demo):
    assert full[1]["unsorted"] == 0 and demo[1]["unsorted"] == 0
    assert full[0].get("/api/needs-look").json()["unsorted"] == 0


def test_an_unreadable_date_is_kept_and_listed_for_a_look(catalog_dir, tmp_path):
    master = read_csv(catalog_dir / "skus_master.csv")
    header = list(master[0].keys())
    r = dict(master[0], last_sale_date="not-a-date")
    csv_text = ",".join(header) + "\n" + ",".join(r[h] if "," not in r[h] else f'"{r[h]}"' for h in header)
    client = make_client(tmp_path)
    res = client.post("/api/upload", content=csv_text.encode(), headers={"content-type": "text/csv"}).json()
    assert res["kept"] == 1 and res["look"] == 1
    assert "UNREADABLE_DATE" in client.get("/api/needs-look").json()["items"][0]["reasons"]


# ------------------------------------------------------------------------------ storage and the no-model rule
def test_storage_is_wal_with_the_required_tables_and_indexes(full, tmp_path_factory):
    client, _ = full
    db = client.app.state.db_path
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"shops", "products", "skus", "uploads", "inventory_snapshots", "sales_events", "dq_issues", "classifications",
            "market_sources", "market_items", "market_events", "alerts", "recommendations", "audit_log"} <= tables
    idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_products_category", "idx_products_line_slug", "idx_skus_status"} <= idx
    cols = {r[1] for r in con.execute("PRAGMA table_info(products)")}
    assert {"category", "line", "line_id", "line_slug", "year", "brand", "model", "variant", "search_text"} <= cols
    con.close()


def test_money_is_stored_as_integers_and_dates_as_iso_text(full):
    con = sqlite3.connect(full[0].app.state.db_path)
    types = con.execute("SELECT DISTINCT typeof(unit_cost_cents), typeof(sell_price_cents), typeof(capital_cents) FROM skus").fetchall()
    assert types == [("integer", "integer", "integer")]
    dates = [r[0] for r in con.execute("SELECT last_sale_date FROM skus WHERE last_sale_date IS NOT NULL LIMIT 500")]
    assert dates and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in dates)
    assert con.execute("SELECT COUNT(*) FROM skus WHERE capital_cents != qty * unit_cost_cents").fetchone()[0] == 0
    con.close()


def test_every_upload_keeps_its_history(full):
    con = sqlite3.connect(full[0].app.state.db_path)
    assert con.execute("SELECT COUNT(*) FROM inventory_snapshots").fetchone()[0] == 3000
    assert con.execute("SELECT COUNT(*) FROM classifications").fetchone()[0] == 3000
    con.close()


def test_no_api_storage_ingest_or_core_module_imports_the_model_client():
    import ast
    root = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in list(root.glob("api/*.py")) + list(root.glob("storage/*.py")) + list(root.glob("core/*.py")) + [root / "ingest.py", root / "catalog.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            if any(n == "openai" or n.startswith("app.llm") or n.startswith("app.agents") or n.startswith("openai.") for n in names):
                offenders.append((path.name, names))
    assert not offenders, offenders


def test_sql_in_the_api_is_parameterized():
    """No f-string or concatenation builds a value into a query; the only f-strings place fixed '?' placeholder lists."""
    root = Path(__file__).resolve().parent.parent / "app"
    for path in [root / "storage" / "repo.py", root / "ingest.py"]:
        for m in re.finditer(r'f"[^"\n]*(SELECT|INSERT|UPDATE|DELETE)[^"\n]*\{([^}]*)\}', path.read_text(encoding="utf-8")):
            assert m.group(2).strip() == "RISK_MARKS" or m.group(2).strip() == "placeholders", (path.name, m.group(0)[:80])


# ------------------------------------------------------------------------------ undo
def test_undo_of_an_accepted_plan_restores_money_at_risk_exactly(demo):
    client, _, _ = demo
    recs = planned(client)
    start = client.get("/api/summary").json()
    client.post(f"/api/actions/{recs[0]['id']}/accept")
    client.post(f"/api/actions/{recs[1]['id']}/accept")
    r = client.post(f"/api/actions/{recs[0]['id']}/undo").json()
    assert r["status"] == "proposed" and r["money_at_risk_cents"] == start["money_at_risk_cents"] - recs[1]["tied_cents"]
    assert r["accepted_cents"] == recs[1]["tied_cents"] and r["accepted_n"] == 1
    again = client.post(f"/api/actions/{recs[0]['id']}/accept").json()                       # accepting again works
    assert again["money_at_risk_cents"] == start["money_at_risk_cents"] - recs[0]["tied_cents"] - recs[1]["tied_cents"]
    client.post(f"/api/actions/{recs[0]['id']}/undo")
    client.post(f"/api/actions/{recs[1]['id']}/undo")
    end = client.get("/api/summary").json()
    assert end["money_at_risk_cents"] == start["money_at_risk_cents"] and end["accepted_cents"] == 0


def test_undo_of_a_declined_plan_lets_the_owner_accept_it(demo):
    client, _, _ = demo
    recs = planned(client)
    client.post(f"/api/actions/{recs[0]['id']}/decline")
    assert client.post(f"/api/actions/{recs[0]['id']}/accept").status_code == 409            # declined is final until undone
    assert client.post(f"/api/actions/{recs[0]['id']}/undo").json()["status"] == "proposed"
    assert client.post(f"/api/actions/{recs[0]['id']}/accept").json()["status"] == "accepted"


def test_double_undo_and_undo_of_an_undecided_plan_are_refused(demo):
    client, _, _ = demo
    recs = planned(client)
    assert client.post(f"/api/actions/{recs[0]['id']}/undo").status_code == 409              # never decided
    client.post(f"/api/actions/{recs[0]['id']}/accept")
    assert client.post(f"/api/actions/{recs[0]['id']}/undo").status_code == 200
    assert client.post(f"/api/actions/{recs[0]['id']}/undo").status_code == 409              # double undo


def test_undo_of_an_unknown_id_is_404(demo):
    assert demo[0].post("/api/actions/99999/undo").status_code == 404


def test_undo_is_written_to_the_audit_log(demo):
    client, _, db = demo
    recs = planned(client)
    client.post(f"/api/actions/{recs[0]['id']}/accept")
    client.post(f"/api/actions/{recs[0]['id']}/undo")
    con = sqlite3.connect(db)
    rows = con.execute("SELECT action, detail_json FROM audit_log ORDER BY id").fetchall()
    con.close()
    assert [r[0] for r in rows][-2:] == ["accepted", "undone"] and '"was": "accepted"' in rows[-1][1]
