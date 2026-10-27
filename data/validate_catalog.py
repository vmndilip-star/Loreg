#!/usr/bin/env python3
"""Validate a generated Loreg database. Exit code 0 only if every check passes.

    python data/validate_catalog.py --dir data/generated                  # validate one folder
    python data/validate_catalog.py --seeds 1-10                          # generate and validate 10 seeds
    python data/validate_catalog.py --seeds 1-10 --skip-determinism       # faster

The hand labels for the fixtures are written out again in this file on purpose: the check is that the
classifier agrees with a person's reading, not with the generator's own table.
"""

import argparse
import csv
import hashlib
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "data"))

from app.core.classify import classify  # noqa: E402
from app.core.quality import CODES, dq_check, parse_date, split_by_severity  # noqa: E402
from app.core.thresholds import load_thresholds  # noqa: E402
import generate_catalog as G  # noqa: E402

COUNTS = {"tv": 800, "phone": 800, "laptop": 450, "washer": 300, "washer_dryer": 150, "refrigerator": 500}
STATUS_TOL = 0.015          # status shares per category: within 1.5 percentage points of the config mix
FIXTURE_CAP = 3000          # dollars of capital per fixture row
HAND_LABELS = {
    "never_sold_new": "new", "never_sold_old": "dead", "never_sold_mid_age": "insufficient_data",
    "no_received_never_sold": "insufficient_data", "no_received_but_sold_slow": "slow",
    "stockout_recent_sale": "healthy", "stockout_dead_history": "healthy", "sold_today_overstock": "slow",
    "dsls_equals_slow_threshold": "slow", "dsls_slow_minus_1": "healthy", "dsls_equals_dead_threshold": "dead",
    "dsls_dead_minus_1": "slow", "seasonal_past_dead_threshold": "slow", "seasonal_past_slow_threshold": "healthy",
    "overpriced_vs_market": "slow", "cost_above_market_dead": "dead", "viral_spike": "healthy",
    "dead_return_window_open": "dead", "dead_return_window_closed": "dead", "launch_family": "slow",
    "price_up_trend": "healthy",
}
LAUNCH_LINES = {"QN90D Neo QLED", "QN85D Neo QLED", "Q80D QLED", "Q70D QLED", "Q60D QLED", "S90D OLED"}


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def d(s):
    return date.fromisoformat(s) if s else None


def validate(folder, seed, as_of_text, anchors_override=None, determinism=True, quiet=False):
    D, as_of = Path(folder), date.fromisoformat(as_of_text)
    th = load_thresholds()
    cfg = yaml.safe_load((REPO / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), str(detail)))
        if not quiet:
            print(("PASS " if ok else "FAIL ") + name + (f"  [{str(detail)[:200]}]" if detail and not ok else ""))

    master, ground = read(D / "skus_master.csv"), read(D / "ground_truth.csv")
    events, mprices = read(D / "sales_events.csv"), read(D / "market_prices.csv")
    hist, summary = read(D / "market_price_history.csv"), read(D / "summary.csv")
    anchors = G.load_anchors(REPO / "data" / "market_anchors.csv", anchors_override)
    by_sku = {r["sku"]: r for r in master}
    gt = {r["sku"]: r for r in ground}
    mp = {r["sku"]: r for r in mprices}
    lines_yaml = yaml.safe_load((REPO / "data" / "catalog_lines.yaml").read_text(encoding="utf-8"))
    variants = {v["sku"]: v for v in G.expand_catalog(REPO / "data" / "catalog_lines.yaml")}

    # ---- identity and counts
    got = {c: sum(1 for r in master if r["category"] == c) for c in COUNTS}
    check("C01 exact counts per category (800/800/450/300/150/500)", got == COUNTS, got)
    check("C02 3,000 rows and 3,000 unique SKUs (case-insensitive)",
          len(master) == 3000 and len({r["sku"].lower() for r in master}) == 3000, len(master))
    check("C03 3,000 unique product names (brand + model)", len({(r["brand"], r["model"]) for r in master}) == 3000)
    expected_lines = {ln["id"]: ln["variant_count"] for c in COUNTS for ln in lines_yaml["categories"][c]["lines"]}
    line_counts = {}
    for r in ground:
        line_counts[r["line_id"]] = line_counts.get(r["line_id"], 0) + 1
    check("C04 every line in catalog_lines.yaml has exactly its variant count", line_counts == expected_lines,
          [k for k in expected_lines if line_counts.get(k) != expected_lines[k]][:3])
    req = ["sku", "brand", "model", "category", "qty_on_hand", "unit_cost", "sell_price", "msrp", "supplier"]
    check("C05 required columns never empty", all(r[c] != "" for r in master for c in req))
    no_recv = {s for s, g in gt.items() if "no_received" in g["edge_tags"]}
    check("C06 blank received_date only on the no-received fixtures", {r["sku"] for r in master if r["received_date"] == ""} == no_recv)
    check("C07 quantities are integers >= 0; cost, price and msrp > 0",
          all(int(r["qty_on_hand"]) >= 0 and float(r["unit_cost"]) > 0 and float(r["sell_price"]) > 0 and float(r["msrp"]) > 0 for r in master))
    check("C08 no date after the as-of date",
          all((d(r[c]) or as_of) <= as_of for r in master for c in ("last_sale_date", "received_date")))
    check("C09 sell price >= unit cost for every row", all(float(r["sell_price"]) >= float(r["unit_cost"]) for r in master))
    check("C10 sell price <= 1.2 x msrp for every row", all(float(r["sell_price"]) <= 1.2 * float(r["msrp"]) for r in master))
    check("C11 is_seasonal is 0 or 1; seasonal rows are fixtures only",
          all(r["is_seasonal"] in ("0", "1") for r in master) and all(gt[r["sku"]]["is_fixture"] == "1" for r in master if r["is_seasonal"] == "1"))

    # ---- market prices and anchors
    check("P01 every SKU has a market price row with a provenance label",
          len(mp) == 3000 and all(m["price_evidence"] in ("sourced_2026", "sourced_older", "estimated") for m in mp.values()))
    check("P02 promo floor <= street price <= msrp for every SKU",
          all(float(m["market_floor_price_today"]) <= float(m["market_street_price"]) <= float(m["msrp_market"]) for m in mp.values()))
    bad_floor = []
    for sku, m in mp.items():
        v, street, promo = variants[sku], float(m["market_street_price"]), float(m["market_floor_price_today"])
        if v["category"] == "tv":
            fs, fp = G.tv_floor(anchors, v["size"], "street"), G.tv_floor(anchors, v["size"], "promo")
        else:
            a_id = m["anchor_id"][:-len("_street")]
            fs, fp = anchors[f"{a_id}_street"]["usd"], anchors[f"{a_id}_promo"]["usd"]
        if street < round(fs, 2) - 0.005 or promo < round(fp, 2) - 0.005:
            bad_floor.append((sku, street, round(fs, 2), promo, round(fp, 2)))
    check("P03 anchor floors: no SKU priced below its cheapest regular or promo anchor", not bad_floor, bad_floor[:3])
    tv_rows = [(variants[s], mp[s]) for s in mp if variants[s]["category"] == "tv"]
    ok_s = ok_p = True
    bad = []
    for size in (43, 50, 55, 65, 75):
        at = [m for v, m in tv_rows if v["size"] == size]
        lo_s, lo_p = min(float(m["market_street_price"]) for m in at), min(float(m["market_floor_price_today"]) for m in at)
        if abs(lo_s - anchors[f"tv_{size}_street"]["usd"]) > 0.005:
            ok_s = False
            bad.append((size, "street", lo_s))
        if abs(lo_p - anchors[f"tv_{size}_promo"]["usd"]) > 0.005:
            ok_p = False
            bad.append((size, "promo", lo_p))
    check("P04 cheapest regular price per anchored TV size equals the street anchor (65in -> 329.99)", ok_s, bad)
    check("P05 cheapest promo price per anchored TV size equals the promo anchor (65in -> 249.99)", ok_p, bad)
    lead = [(v, m) for v, m in tv_rows if v["leader"]]
    on_anchor = all(abs(float(m["market_street_price"]) - anchors[f"tv_{v['size']}_street"]["usd"]) < 0.005
                    for v, m in lead if v["size"] in (43, 50, 55, 65, 75))
    check("P06 Insignia F50 and Toshiba C350 lines sit exactly on the anchors",
          on_anchor and {v["brand"] for v, _ in lead} == {"Insignia", "Toshiba"}, len(lead))
    sizes = [43, 50, 55, 65, 75, 85]
    check("P07 TV anchors strictly increase with size, and every promo anchor is below its street anchor",
          all(anchors[f"tv_{a}_{k}"]["usd"] < anchors[f"tv_{b}_{k}"]["usd"] for a, b in zip(sizes, sizes[1:]) for k in ("street", "promo"))
          and all(anchors[f"tv_{s}_promo"]["usd"] < anchors[f"tv_{s}_street"]["usd"] for s in sizes))

    def order_key(v):
        return v["size"]
    groups = {}
    for sku, v in variants.items():
        groups.setdefault((v["line_id"], v["other"]), []).append(v)
    unordered = []
    for key, vs in groups.items():
        vs.sort(key=order_key)
        prices = [float(mp[v["sku"]]["market_street_price"]) for v in vs]
        if any(b < a - 0.005 for a, b in zip(prices, prices[1:])):
            unordered.append((key[0], [v["size"] for v in vs], prices))
    check("P08 size ordering: within a line a bigger size never costs less", not unordered, unordered[:2])
    by_sku_hist = {}
    for h in hist:
        by_sku_hist.setdefault(h["sku"], []).append(h)
    check("P09 every SKU has 13 monthly history points and month 0 equals today's price",
          len(by_sku_hist) == 3000 and all(len(x) == 13 and float(x[0]["market_street_price"]) == float(mp[s]["market_street_price"])
                                           for s, x in by_sku_hist.items()))
    share_above = sum(int(m["cost_above_market"]) for m in mp.values()) / 3000
    check("P10 stock bought above today's market is 3%-40% of SKUs", 0.03 <= share_above <= 0.40, f"{share_above:.1%}")
    up = [s for s, v in variants.items() if v["price_up"]]
    check("P11 Moto G 5G (2024) and Moto G Power 5G (2024) show a rising price trend",
          len(up) > 0 and {variants[s]["line"] for s in up} == {"Moto G 5G", "Moto G Power 5G"}
          and all("price_up_trend" in mp[s]["price_basis"] and float(by_sku_hist[s][0]["market_street_price"]) > float(by_sku_hist[s][12]["market_street_price"])
                  for s in up), len(up))
    check("P12 every sourced anchor has a URL; every anchor is labelled",
          all(a["evidence"] in ("sourced_2026", "sourced_older", "estimated") and (a["evidence"] == "estimated" or a["source_url"]) for a in anchors.values()))

    # ---- sales consistency
    sales = [e for e in events if e["event_type"] == "sale"]
    returns = [e for e in events if e["event_type"] == "return"]
    last, u30, u90 = {}, {}, {}
    for e in sales:
        ed, q = d(e["event_date"]), int(e["qty"])
        last[e["sku"]] = max(last.get(e["sku"], ed), ed)
        age = (as_of - ed).days
        u30[e["sku"]] = u30.get(e["sku"], 0) + (q if age < 30 else 0)
        u90[e["sku"]] = u90.get(e["sku"], 0) + (q if age < 90 else 0)
    check("S01 last_sale_date equals the latest sale event; SKUs with no last sale have no sale events",
          all((d(r["last_sale_date"]) == last.get(r["sku"])) for r in master))
    check("S02 units_sold_30d and units_sold_90d equal the sale events",
          all(int(r["units_sold_30d"]) == u30.get(r["sku"], 0) and int(r["units_sold_90d"]) == u90.get(r["sku"], 0) for r in master))
    check("S03 every event belongs to a SKU in the master, and no sale is before the received date",
          all(e["sku"] in by_sku and (not by_sku[e["sku"]]["received_date"] or d(e["event_date"]) >= d(by_sku[e["sku"]]["received_date"]))
              for e in events))
    check("S04 events span at most 12 months and none are in the future",
          all(as_of - timedelta(days=365) <= d(e["event_date"]) <= as_of for e in events))
    sale_units, return_units = sum(int(e["qty"]) for e in sales), sum(int(e["qty"]) for e in returns)
    check("S05 customer returns exist and are under 5% of units sold", 0 < return_units < 0.05 * sale_units, f"{return_units}/{sale_units}")

    # ---- classification
    check("K01 expected status equals computed status for ALL 3,000 rows",
          all(g["expected_status"] == g["computed_status"] for g in ground),
          [(g["sku"], g["expected_status"], g["computed_status"]) for g in ground if g["expected_status"] != g["computed_status"]][:3])
    recomputed = []
    for r in master:
        recomputed.append(classify(int(r["qty_on_hand"]), d(r["received_date"]), d(r["last_sale_date"]), int(r["units_sold_90d"]),
                                   r["category"], bool(int(r["is_seasonal"])), as_of, th))
    check("K02 re-classifying straight from the CSV matches ground truth", recomputed == [gt[r["sku"]]["computed_status"] for r in master])
    fixtures = [g for g in ground if g["is_fixture"] == "1"]
    tags = {g["edge_tags"].split(";")[0] for g in fixtures}
    check("K03 every required edge case is present at least once", set(HAND_LABELS) <= tags, sorted(set(HAND_LABELS) - tags))
    check("K04 every fixture's computed status equals the hand label for its tag",
          all(g["computed_status"] == HAND_LABELS[g["edge_tags"].split(";")[0]] for g in fixtures),
          [(g["sku"], g["edge_tags"].split(";")[0], g["computed_status"]) for g in fixtures
           if g["computed_status"] != HAND_LABELS[g["edge_tags"].split(";")[0]]][:3])
    # the threshold fixtures sit exactly where their name says
    th_ok = True
    for g in fixtures:
        tag, r = g["edge_tags"].split(";")[0], by_sku[g["sku"]]
        t = cfg["categories"][r["category"]]
        dsls = (as_of - d(r["last_sale_date"])).days if r["last_sale_date"] else None
        want = {"dsls_equals_slow_threshold": t["slow_dsls"], "dsls_slow_minus_1": t["slow_dsls"] - 1,
                "dsls_equals_dead_threshold": t["dead_dsls"], "dsls_dead_minus_1": t["dead_dsls"] - 1}.get(tag)
        if want is not None and dsls != want:
            th_ok = False
    check("K05 threshold fixtures sit exactly at, and one day short of, the configured thresholds", th_ok)
    check("K06 all five statuses are present", {g["computed_status"] for g in ground} == {"dead", "slow", "healthy", "new", "insufficient_data"})
    check("K07 at least one dead SKU is still inside its supplier return window, and one is outside",
          any("dead_return_window_open" in g["edge_tags"] for g in ground) and any("dead_return_window_closed" in g["edge_tags"] for g in ground))
    over = [(g["sku"], g["capital_at_cost"]) for g in fixtures if float(g["capital_at_cost"]) > FIXTURE_CAP]
    check("K08 every fixture row's capital (qty x cost) is at most $3,000", not over, over[:3])
    share = len(fixtures) / 3000
    check("K09 fixture rows are about 1% of SKUs (0.8% to 1.4%)", 0.008 <= share <= 0.014, f"{share:.2%}")
    launch = [s for s, v in variants.items() if v["launch"]]
    check("K10 launch family is exactly the six Samsung 2024 TV lines",
          len(launch) > 0 and all(variants[s]["brand"] == "Samsung" and variants[s]["year"] == 2024 and variants[s]["category"] == "tv"
                                  and variants[s]["line"] in LAUNCH_LINES for s in launch)
          and {variants[s]["line"] for s in launch} == LAUNCH_LINES and all(gt[s]["launch_family"] == "1" for s in launch), len(launch))

    # ---- status mix and headline shares
    mix = cfg["generator"]["status_mix"]
    worst = 0.0
    for cat, n in COUNTS.items():
        rows = [g for g in ground if g["category"] == cat]
        for status, key in (("dead", "dead"), ("slow", "slow"), ("new", "new")):
            share_c = sum(1 for g in rows if g["computed_status"] == status) / n
            worst = max(worst, abs(share_c - mix[key]))
    check("M01 dead, slow and new shares per category are within 1.5 points of the configured mix", worst <= STATUS_TOL, f"worst {worst:.3%}")
    total = next(r for r in summary if r["category"] == "TOTAL")
    dead_share, risk_share = float(total["dead_pct"].rstrip("%")) / 100, (float(total["dead_pct"].rstrip("%")) + float(total["slow_pct"].rstrip("%"))) / 100
    check("M02 dead capital is 5%-25% of stock at cost; dead plus slow is 15%-55%", 0.05 <= dead_share <= 0.25 and 0.15 <= risk_share <= 0.55,
          f"dead {dead_share:.1%}, dead+slow {risk_share:.1%}")
    ex = float(total["dead_pct_ex_fixtures"].rstrip("%")) / 100
    check("M03 fixture rows move the dead capital share by less than 1 point", abs(ex - dead_share) < 0.01, f"with {dead_share:.2%} without {ex:.2%}")
    recomputed_total = round(sum(int(r["qty_on_hand"]) * float(r["unit_cost"]) for r in master))
    check("M04 summary.csv stock at cost equals the sum from skus_master.csv", int(total["stock_at_cost"]) == recomputed_total)

    # ---- uploads and data quality
    master_rows = [dict(r) for r in master]
    issues = dq_check(master_rows, as_of, th)
    viral = {s for s, g in gt.items() if "viral_spike" in g["edge_tags"]}
    bad_dq = [(i.row, i.code) for i in issues if i.severity in ("error", "warning") and not (i.code == "VELOCITY_OUTLIER" and master[i.row - 1]["sku"] in viral)]
    check("U01 the clean 3,000 raises no data-quality error (and no warning except viral-spike velocity)", not bad_dq, bad_dq[:3])
    check("U02 no error-severity issue of any kind in the clean 3,000", not any(i.severity == "error" for i in issues))
    demo = read(D / "upload_demo_200.csv")
    diss = dq_check(demo, as_of, th)
    err_rows, warn_rows, _ = split_by_severity(diss)
    err_codes = sorted({i.code for i in diss if i.severity == "error"})
    check("U03 upload_demo_200.csv has 200 rows and exactly 3 seeded errors (missing cost, negative qty, duplicate SKU)",
          len(demo) == 200 and len(err_rows) == 3 and err_codes == ["DUPLICATE_SKU", "MISSING_COST", "NEGATIVE_QTY"], (len(demo), err_codes))
    demo_cat, seen_demo = {}, set()
    for r in demo:
        if r["sku"] not in seen_demo:           # count each product once; the 200th row repeats one of them
            seen_demo.add(r["sku"])
            demo_cat[r["category"]] = demo_cat.get(r["category"], 0) + 1
    n_launch = sum(1 for sku in seen_demo if sku in set(launch))
    check("U04 demo upload mix: 80 TVs, 62 phones, 25 laptops, 20 washers, 12 washer-dryers (199 products) plus 1 duplicate row",
          demo_cat == {"tv": 80, "phone": 62, "laptop": 25, "washer": 20, "washer_dryer": 12} and len(demo) - len(seen_demo) == 1, demo_cat)
    check("U05 demo upload includes exactly 24 Samsung launch-family TVs", n_launch == 24, n_launch)
    defects = read(D / "upload_defects.csv")
    man = read(D / "defects_manifest.csv")
    dis = dq_check(defects, as_of, th)
    by_row = {}
    for i in dis:
        by_row.setdefault(i.row, set()).add(i.code)
    missed = []
    for m in man:
        got_codes, code = by_row.get(int(m["data_row"]), set()), m["expected_code"]
        ok = not (got_codes - {"ZERO_QTY"}) if code.startswith("OK_") else code in got_codes
        if not ok:
            missed.append((m["data_row"], code, sorted(got_codes)))
    check("U06 every injected defect is detected, and the 4 format cases parse without any issue", not missed, missed)
    check("U07 the defects file covers all 11 data-quality codes",
          {m["expected_code"] for m in man if not m["expected_code"].startswith("OK_")} == set(CODES))
    check("U08 four format cases present (MM/DD/YYYY, $1,299.00, category synonym, 5.0 quantity)",
          {m["expected_code"] for m in man if m["expected_code"].startswith("OK_")} ==
          {"OK_US_DATE_FORMAT", "OK_CURRENCY_STRING", "OK_CATEGORY_SYNONYM", "OK_FLOAT_QTY"})

    # ---- determinism
    if determinism:
        def digest(p):
            return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(Path(p).glob("*.csv"))}
        with tempfile.TemporaryDirectory() as t:
            cmd = [sys.executable, str(REPO / "data" / "generate_catalog.py"), "--seed", str(seed), "--as-of", as_of_text, "--out", t]
            if anchors_override:
                cmd += ["--anchors", str(anchors_override)]
            subprocess.run(cmd, check=True, capture_output=True)
            a, b = digest(D), digest(t)
            same = {k for k in b if k in a and a[k] == b[k]}
            check("D01 re-running with the same arguments reproduces every CSV byte for byte", same == set(b) and bool(b),
                  sorted(set(b) - same))
    return checks


def parse_seeds(text):
    if "-" in text:
        a, b = text.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in text.split(",")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(REPO / "data" / "generated"))
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--seeds", default=None, help='generate and validate several seeds, e.g. "1-10"')
    ap.add_argument("--as-of", default="2026-10-26")
    ap.add_argument("--anchors", default=None)
    ap.add_argument("--skip-determinism", action="store_true")
    a = ap.parse_args()
    if not a.seeds:
        checks = validate(a.dir, a.seed, a.as_of, a.anchors, not a.skip_determinism)
        passed = sum(ok for _, ok, _ in checks)
        print(f"\n{passed}/{len(checks)} checks passed")
        sys.exit(0 if passed == len(checks) else 1)
    results = {}
    for seed in parse_seeds(a.seeds):
        with tempfile.TemporaryDirectory() as t:
            cmd = [sys.executable, str(REPO / "data" / "generate_catalog.py"), "--seed", str(seed), "--as-of", a.as_of, "--out", t]
            if a.anchors:
                cmd += ["--anchors", a.anchors]
            gen = subprocess.run(cmd, capture_output=True, text=True)
            if gen.returncode:
                results[seed] = [("GEN generator crashed", False, gen.stderr.strip().splitlines()[-1])]
                continue
            total = next(r for r in read(Path(t) / "summary.csv") if r["category"] == "TOTAL")
            results[seed] = validate(t, seed, a.as_of, a.anchors, not a.skip_determinism, quiet=True)
            results[seed].append(("INFO", True, f"stock ${int(total['stock_at_cost']):,}  dead ${int(total['dead_capital']):,} ({total['dead_pct']})  "
                                                f"slow ${int(total['slow_capital']):,} ({total['slow_pct']})  | without fixtures: dead {total['dead_pct_ex_fixtures']} slow {total['slow_pct_ex_fixtures']}"))
        checks = [c for c in results[seed] if c[0] != "INFO"]
        failed = [c for c in checks if not c[1]]
        info = next((c[2] for c in results[seed] if c[0] == "INFO"), "")
        print(f"seed {seed:>3}: {len(checks) - len(failed)}/{len(checks)} checks passed  {info}")
        for n, _, det in failed:
            print(f"          FAIL {n}  [{det[:160]}]")
    bad = sum(1 for cs in results.values() for c in cs if c[0] != "INFO" and not c[1])
    print(f"\n{len(results)} seeds, {bad} failed checks in total")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
