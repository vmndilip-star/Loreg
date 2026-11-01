"""Sales history: POST /api/sales/upload and GET /api/categories/{category}/sales, and the demo's synthetic history. Offline, no model.

The window, month and year figures are checked on a small hand-written history whose answers are worked out here by hand."""

import csv
import io
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.api.main import create_app
from app.demo import DEMO_FILE, DEMO_SALES_FILE
from auth import AuthedClient

pytestmark = pytest.mark.usefixtures("no_model")
ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 10, 26)
SKU = "TV-SON-0419"            # a Sony TV in the demo stock list
HEADER = "sku,date,qty,unit_price,type\n"


def days_ago(n: int) -> str:
    return (AS_OF - timedelta(days=n)).isoformat()


def send(c, text, name="sales.csv"):
    return c.post("/api/sales/upload", files={"file": (name, text if isinstance(text, bytes) else text.encode(), "text/csv")})


@pytest.fixture
def shop(tmp_path):
    c = AuthedClient(create_app(tmp_path / "s.sqlite", as_of=AS_OF.isoformat()))
    assert c.post("/api/upload", files={"file": ("d.csv", DEMO_FILE.read_bytes(), "text/csv")}).status_code == 200
    return c


def stock_rows() -> dict:
    with open(DEMO_FILE, newline="", encoding="utf-8-sig") as f:
        return {r["sku"]: r for r in csv.DictReader(f)}


# ---- the demo's synthetic history ---------------------------------------------------------------

def test_the_demo_history_agrees_with_the_demo_stock_list_for_every_item():
    stock = stock_rows()
    sold = {}
    with open(DEMO_SALES_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows and {r["type"] for r in rows} == {"sale"}
    for r in rows:
        sold.setdefault(r["sku"], []).append((date.fromisoformat(r["date"]), int(r["qty"])))
    assert set(sold) <= set(stock)
    for sku, s in stock.items():
        if not s["unit_cost"] or int(s["qty_on_hand"]) < 0:
            continue                                                             # rows the stock upload blocks
        mine = sold.get(sku, [])
        in_days = lambda n: sum(q for d, q in mine if d > AS_OF - timedelta(days=n))   # noqa: E731
        assert in_days(30) == int(s["units_sold_30d"] or 0) and in_days(90) == int(s["units_sold_90d"] or 0), sku
        assert (max(d for d, _ in mine).isoformat() if mine else "") == s["last_sale_date"], sku
        assert all(date(2024, 11, 1) <= d <= AS_OF and q >= 1 for d, q in mine), sku


def test_the_demo_history_file_is_what_the_script_makes(tmp_path):
    out = tmp_path / "again.csv"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_demo_sales.py"), "--out", str(out)], check=True, capture_output=True)
    assert out.read_bytes() == DEMO_SALES_FILE.read_bytes()                     # no drift from the stock list it is made from


# ---- the upload ----------------------------------------------------------------------------------

def test_good_rows_are_kept_bad_rows_are_blocked_with_a_reason_and_unknown_skus_are_counted(shop):
    text = HEADER + "\n".join([
        f"{SKU},{days_ago(0)},2,500.00,sale",
        f"{SKU},{days_ago(1)},1,$500.00,",                                     # blank type means sale; a $ is fine
        f"{SKU},10/01/2026,1,500,return",
        f"NOT-IN-STOCK,{days_ago(3)},1,10,sale",
        f"{SKU},{(AS_OF + timedelta(days=1)).isoformat()},1,500,sale",          # after the stock date
        f"{SKU},2026-13-01,1,500,sale",
        f"{SKU},{days_ago(2)},0,500,sale", f"{SKU},{days_ago(2)},1.5,500,sale", f"{SKU},{days_ago(2)},inf,500,sale", f"{SKU},{days_ago(2)},-1,500,sale",
        f"{SKU},{days_ago(2)},1,-5,sale", f"{SKU},{days_ago(2)},1,,sale", f"{SKU},{days_ago(2)},1,500,refund", f",{days_ago(2)},1,500,sale",
    ]) + "\n"
    r = send(shop, text)
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["rows"], b["kept"], b["blocked"], b["skus"], b["not_in_stock_list"]) == (14, 4, 10, 2, 1)
    assert (b["first_date"], b["last_date"], b["as_of"], b["synthetic"]) == ("2026-10-01", days_ago(0), AS_OF.isoformat(), False)
    problems = {i["row"]: i["problems"][0] for i in b["issues"]}
    assert "after the stock list's date" in problems[5] and "unreadable" in problems[6] and "sale or return" in problems[13]
    assert all("whole number" in problems[n] for n in (7, 8, 9, 10)) and "unit price" in problems[11] and "unit price" in problems[12]
    assert "SKU is missing" in problems[14]


def test_each_upload_replaces_the_history_and_a_file_with_no_usable_row_keeps_the_old_one(shop):
    assert send(shop, HEADER + f"{SKU},{days_ago(0)},3,500,sale\n").status_code == 200
    assert send(shop, HEADER + f"{SKU},{days_ago(0)},1,500,sale\n").status_code == 200
    assert shop.get("/api/categories/tv/sales").json()["windows"][0]["units"] == 1                # replaced, not added
    bad = send(shop, HEADER + f"{SKU},{days_ago(0)},0,500,sale\n")
    assert bad.status_code == 422 and "nothing was stored" in bad.json()["detail"] and "row 1" in bad.json()["detail"]
    assert shop.get("/api/categories/tv/sales").json()["windows"][0]["units"] == 1                # the old history stays
    for text in ("sku,day,qty\nX,2026-10-01,1\n", HEADER, "", b"sku,date\xff\n"):
        assert send(shop, text).status_code == 422, text


def test_a_sales_history_needs_a_stock_list_first(tmp_path):
    c = AuthedClient(create_app(tmp_path / "n.sqlite", as_of=AS_OF.isoformat()))
    r = send(c, HEADER + f"{SKU},{days_ago(0)},1,500,sale\n")
    assert r.status_code == 409 and "stock list first" in r.json()["detail"]


def test_a_raw_csv_body_works_too_and_a_huge_file_is_413(shop):
    r = shop.post("/api/sales/upload", content=(HEADER + f"{SKU},{days_ago(0)},1,500,sale\n").encode(), headers={"x-filename": "raw.csv"})
    assert r.status_code == 200 and r.json()["file"] == "raw.csv"
    assert shop.post("/api/sales/upload", content=b"x" * (5 * 1024 * 1024 + 1)).status_code == 413


# ---- the figures ---------------------------------------------------------------------------------

def test_windows_months_and_years_on_a_history_worked_out_by_hand(shop):
    rows = [(0, 2, "sale"), (6, 1, "sale"), (7, 4, "sale"), (29, 1, "sale"), (30, 8, "sale"), (364, 16, "sale"), (365, 32, "sale"),
            (3, 1, "return")]
    assert send(shop, HEADER + "".join(f"{SKU},{days_ago(n)},{q},100.00,{t}\n" for n, q, t in rows)).status_code == 200
    h = shop.get("/api/categories/tv/sales").json()
    assert h["source"] == "history" and h["first_date"] == days_ago(365) and h["synthetic"] is False and h["file"] == "sales.csv"
    w = {x["key"]: x for x in h["windows"]}
    assert [w[k]["units"] for k in ("1d", "7d", "30d", "365d")] == [2, 3, 8, 32]   # 0 | +6 | +7, 29 | +30, 364 (365 days ago is outside)
    assert w["7d"]["returned"] == 1 and w["7d"]["revenue_cents"] == (3 - 1) * 10000 and w["7d"]["from"] == days_ago(6)
    assert all(x["covered"] for x in h["windows"])
    months = {m["month"]: m for m in h["monthly"]}
    assert len(h["monthly"]) == 12 and h["monthly"][-1]["month"] == "2026-10" and h["monthly"][0]["month"] == "2025-11"
    assert months["2026-10"]["units"] == 2 + 1 + 4 and months["2026-10"]["partial"] and months["2026-10"]["to"] == "2026-10-26"
    assert months["2026-09"]["units"] == 1 + 8 and not months["2026-09"]["partial"]              # 29 and 30 days ago are Sep 27 and 26
    assert months["2025-11"]["units"] == 0 and months["2025-11"]["covered"]                     # 364 days ago is Oct 27, 2025: before the 12 months
    years = {y["year"]: y for y in h["yearly"]}
    assert set(years) == {2025, 2026} and years[2025]["units"] == 48 and years[2025]["from"] == days_ago(365) and years[2025]["partial"]
    assert years[2026]["units"] == 16 and years[2026]["to"] == AS_OF.isoformat() and years[2026]["bar_pct"] == 33 and years[2025]["bar_pct"] == 100
    assert h["check_30d"] == {"history": 8, "stock_list": 122, "match": False}                  # the stock list says 122 TVs in 30 days


def test_a_month_before_the_history_starts_is_not_counted_as_zero(shop):
    assert send(shop, HEADER + f"{SKU},{days_ago(40)},1,100,sale\n").status_code == 200
    months = shop.get("/api/categories/tv/sales").json()["monthly"]
    assert [m["covered"] for m in months] == [False] * 10 + [True, True] and months[10]["partial"]   # Sep 2026 starts on the 16th


def test_one_item_and_the_stock_list_figures_without_any_history(shop):
    h = shop.get("/api/categories/tv/sales").json()
    assert h["source"] == "stock_list" and "windows" not in h and "No sales history" in h["note"]
    assert h["stock_list"] == {"sold_30d": 122, "sold_90d": 317, "month_before_avg": 97.5, "bar_pct_30d": 100, "bar_pct_before": 80}
    one = shop.get("/api/categories/tv/sales", params={"sku": SKU}).json()
    assert one["item"]["sku"] == SKU and one["stock_list"]["sold_30d"] == int(stock_rows()[SKU]["units_sold_30d"])
    assert shop.get("/api/categories/tv/sales", params={"sku": "PH-APP-0159"}).status_code == 404     # a phone is not one of the TVs
    assert shop.get("/api/categories/tv/sales", params={"sku": "x" * 65}).status_code == 422
    assert shop.get("/api/categories/refrigerator/sales").status_code == 404


def test_the_demo_load_brings_its_sales_history_and_the_reset_brings_it_back(tmp_path, monkeypatch):
    from auth import seed_demo, sign_in_demo
    from fastapi.testclient import TestClient
    monkeypatch.setenv("LOREG_DEMO", "1")
    app = create_app(tmp_path / "d.sqlite", as_of=AS_OF.isoformat())
    seed_demo(app)
    c = TestClient(app)
    sign_in_demo(c)
    loaded = c.post("/api/demo/load").json()
    assert loaded["kept"] == 197 and loaded["sales"]["synthetic"] is True and loaded["sales"]["blocked"] == 0
    first = c.get("/api/categories/tv/sales").json()
    assert first["synthetic"] and first["file"] == DEMO_SALES_FILE.name and all(c.get(f"/api/categories/{k}/sales").json()["check_30d"]["match"]
                                                                             for k in ("tv", "phone", "laptop", "washer", "washer_dryer"))
    assert send(c, HEADER + f"{SKU},{days_ago(0)},1,500,sale\n").status_code == 200
    reset = c.post("/api/demo/reset").json()
    assert reset["deleted"]["sales_events"] == 1 and reset["sales"]["kept"] == loaded["sales"]["kept"]
    assert c.get("/api/categories/tv/sales").json() == first
