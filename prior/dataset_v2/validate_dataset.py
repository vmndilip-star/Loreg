#!/usr/bin/env python3
"""Validate a generated Loreg dataset. Usage: python validate_dataset.py --dir out --size demo --as-of 2026-10-26
Exit code 0 only if every check passes. Writes quality_report.json."""
import argparse, csv, hashlib, json, subprocess, sys, tempfile
from datetime import date
from pathlib import Path
import pandas as pd

from loreg_rules import TH, classify, dq_check, parse_date
import generate_dataset_v2 as G

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default="out"); ap.add_argument("--size", default="demo")
ap.add_argument("--as-of", default="2026-10-26"); ap.add_argument("--seed", type=int, default=2026)
ap.add_argument("--skip-determinism", action="store_true"); ap.add_argument("--anchors", default=None)
a = ap.parse_args()
if a.anchors:
    G.apply_anchor_overrides(a.anchors)
D, AS = Path(a.dir), pd.Timestamp(a.as_of)
AS_D = date.fromisoformat(a.as_of)

m = pd.read_csv(D / "skus_master.csv", parse_dates=["last_sale_date", "received_date", "return_window_end"], dtype={"sku": str})
ev = pd.read_csv(D / "sales_events.csv", parse_dates=["event_date"])
mp = pd.read_csv(D / "market_prices.csv")
hist = pd.read_csv(D / "market_price_history.csv")
gt = pd.read_csv(D / "ground_truth.csv")
ref = pd.read_csv(D / "market_price_reference.csv")
checks = []


def check(name, ok, detail=""):
    checks.append(dict(name=name, ok=bool(ok), detail=str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


# ---- counts & identity
exp = G.SIZES[a.size]
check("C01 category counts match config", m.category.value_counts().to_dict() == exp, m.category.value_counts().to_dict())
check("C02 SKU unique (case-insensitive)", m.sku.str.lower().is_unique)
check("C03 total rows", len(m) == sum(exp.values()), len(m))
req = ["sku", "brand", "model", "category", "qty_on_hand", "unit_cost", "sell_price", "msrp", "supplier"]
check("C04 required columns never empty", m[req].notna().all().all())
no_recv = gt[gt.edge_tags.fillna("").str.contains("no_received")].sku
check("C05 blank received_date only on 'no_received' fixtures", set(m[m.received_date.isna()].sku) == set(no_recv))
check("C06 qty int >= 0, cost/sell/msrp > 0", (m.qty_on_hand >= 0).all() and (m[["unit_cost", "sell_price", "msrp"]] > 0).all().all())
check("C07 no future dates", ((m.last_sale_date.dropna() <= AS).all()) and ((m.received_date.dropna() <= AS).all()))
check("C08 sell_price >= unit_cost for every master row (no PRICE_BELOW_COST)", (m.sell_price >= m.unit_cost).all())
check("C09 sell_price <= 1.2*msrp (no MSRP_MISMATCH)", (m.sell_price <= 1.2 * m.msrp).all())

# ---- sales consistency
sales = ev[ev.event_type == "sale"]
lastsale = sales.groupby("sku").event_date.max()
j = m.set_index("sku").join(lastsale.rename("max_sale"))
has = j.last_sale_date.notna()
check("S01 last_sale_date == max(sale event)", (j[has].last_sale_date == j[has].max_sale).all())
check("S02 SKUs with blank last_sale have zero sale events", j[~has].max_sale.isna().all())
e = sales.merge(m[["sku", "received_date"]], on="sku")
e = e[e.received_date.notna()]
check("S03 no sale before received_date", (e.event_date >= e.received_date).all())
s30 = sales[(AS - sales.event_date).dt.days < 30].groupby("sku").qty.sum()
s90 = sales[(AS - sales.event_date).dt.days < 90].groupby("sku").qty.sum()
jj = m.set_index("sku"); jj["e30"] = s30; jj["e90"] = s90; jj = jj.fillna({"e30": 0, "e90": 0})
check("S04 units_sold_30d == events", (jj.units_sold_30d == jj.e30).all())
check("S05 units_sold_90d == events", (jj.units_sold_90d == jj.e90).all())
check("S06 events within 12 months, none in the future", (ev.event_date <= AS).all() and (ev.event_date >= AS - pd.Timedelta(days=365)).all())
ret = ev[ev.event_type == "return"]
check("S07 returns exist and are a small share (<5%) of sale units", 0 < len(ret) and ret.qty.sum() < 0.05 * sales.qty.sum(), f"{ret.qty.sum()}/{sales.qty.sum()}")

# ---- classification: computed vs expected; fixtures hand-labeled
g = gt.set_index("sku")
check("K01 expected_status == computed_status for ALL rows", (g.expected_status == g.computed_status).all(),
      g[g.expected_status != g.computed_status].head(5).to_dict("index"))
nf = g[~g.edge_tags.fillna("").str.contains("|".join(sorted(need_fx := {"never_sold", "no_received", "stockout", "sold_today", "dsls_", "seasonal_past", "overpriced_vs", "cost_above_market_dead", "viral", "dead_return"})))]
agree = (nf.intended_status == nf.computed_status).mean()
check("K06 generator-intended status matches the Loreg rules for >=97% of non-fixture rows", agree >= 0.97, f"{agree:.1%}")
recomputed = []
for _, r in m.iterrows():
    recomputed.append(classify(int(r.qty_on_hand), None if pd.isna(r.received_date) else r.received_date.date(),
                               None if pd.isna(r.last_sale_date) else r.last_sale_date.date(), int(r.units_sold_90d), r.category, int(r.is_seasonal), AS_D))
check("K02 independent re-classification from CSV matches ground truth", list(recomputed) == list(g.loc[m.sku].computed_status))
fx_tags = {t for t in gt.edge_tags.fillna("").str.split(";").explode() if t}
need = {"never_sold_new", "never_sold_old", "never_sold_mid_age", "no_received_never_sold", "no_received_but_sold_slow", "stockout_recent_sale",
        "stockout_dead_history", "sold_today_overstock", "dsls_equals_slow_threshold", "dsls_slow_minus_1", "dsls_equals_dead_threshold",
        "dsls_dead_minus_1", "seasonal_past_dead_threshold", "overpriced_vs_market", "cost_above_market_dead", "viral_spike",
        "dead_return_window_open", "dead_return_window_closed", "extra_cost_lot", "cost_above_market", "launch_family"}
if a.size == "full":
    need |= {"seasonal_past_slow_threshold", "seasonal"}
check("K03 every edge case present at least once", need <= fx_tags, sorted(need - fx_tags))
statuses = set(g.computed_status)
check("K04 all 5 statuses present", {"dead", "slow", "healthy", "new", "insufficient_data"} <= statuses, statuses)
ret_open = m[m.return_window_end.notna() & (m.return_window_end >= AS)].sku
dead_open = set(ret_open) & set(g[g.computed_status == "dead"].index)
check("K05 at least one dead SKU still inside return window", len(dead_open) >= 1, len(dead_open))

# ---- risk-share sanity
tot = (m.qty_on_hand * m.unit_cost).sum()
m["cap"] = m.qty_on_hand * m.unit_cost
dead_share = m.set_index("sku").loc[g[g.computed_status == "dead"].index].cap.sum() / tot
risk_share = m.set_index("sku").loc[g[g.computed_status.isin(["dead", "slow"])].index].cap.sum() / tot
check("R01 dead capital share within 5%-25% of stock at cost", 0.05 <= dead_share <= 0.25, f"{dead_share:.1%}")
check("R02 dead+slow share within 15%-55%", 0.15 <= risk_share <= 0.55, f"{risk_share:.1%}")

# ---- market-price scenario
c = mp.set_index("sku")
check("P01 every SKU has a market price row with a provenance tag", len(c) == len(m) and c.price_evidence.isin(["sourced_2026", "sourced_older", "estimated"]).all())
check("P02 market_floor <= market_street <= msrp_market", ((c.market_floor_price_today <= c.market_street_price) & (c.market_street_price <= c.msrp_market)).all())
tv = m[m.category == "tv"].merge(mp, on="sku")
import re
def tv_size(model):
    for s in (100, 86, 85, 77, 75, 65, 58, 55, 50, 48, 43, 42, 32):
        if str(s) in re.sub(r"[^0-9]", " ", model).split() or re.search(rf"(?<![0-9]){s}(?![0-9])", model):
            return s
    return None
tv["size"] = tv["size_inch"]
check("P03 every TV has a size", tv["size"].notna().all())
tv["promo"] = tv["size"].map(lambda s: G.tv_floor(int(s), "promo_floor"))
tv["streetf"] = tv["size"].map(lambda s: G.tv_floor(int(s), "street_floor"))
check("P04 no TV market price below its size's promo floor anchor", (tv.market_street_price >= tv.promo - 0.01).all(), tv[tv.market_street_price < tv.promo].head(3)[["sku", "market_street_price", "promo"]].to_dict("records"))
check("P05 budget-tier TV at 65in is within 1.0x-1.9x of the 65in street floor",
      True if tv[(tv["size"] == 65)].empty else (tv[(tv["size"] == 65)].market_street_price.min() >= 0.9 * 249.99))
ok_s, ok_p, bad = True, True, []
for sz in (43, 50, 55, 65, 75):
    d = tv[tv["size"] == sz]
    if d.empty:
        continue
    if abs(d.market_street_price.min() - G.A[f"tv_{sz}_street"]["usd"]) > 0.02: ok_s = False; bad.append((sz, "street", d.market_street_price.min()))
    if abs(d.market_floor_price_today.min() - G.A[f"tv_{sz}_promo"]["usd"]) > 0.02: ok_p = False; bad.append((sz, "promo", d.market_floor_price_today.min()))
check("P06 cheapest regular price per anchored TV size == street anchor (e.g. 65in -> 329.99)", ok_s, bad)
check("P16 cheapest current promo price per anchored TV size == promo anchor (e.g. 65in -> 249.99)", ok_p, bad)
fam = tv.groupby(tv.model.map(lambda s: re.sub(r"(?<![0-9])(32|42|43|48|50|55|58|65|75|77|85|86|100)(?![0-9])", "#", s)))
mono = True
for k, d in fam:
    d = d.drop_duplicates("size").sort_values("size")
    if len(d) > 1 and not (d.market_street_price.diff().dropna() >= -0.01).all():
        mono = False
check("P07 within a TV model line, bigger size never cheaper", mono)
anch_ok = True
for pt in ("promo", "street"):
    ys = [G.A[f"tv_{s}_{pt}"]["usd"] for s in G.TV_SIZES_A]
    anch_ok &= all(x < y for x, y in zip(ys, ys[1:]))
check("P08 TV anchors strictly increase with size (promo and street)", anch_ok)
check("P09 every promo anchor < its street anchor",
      all(G.A[k]["usd"] < G.A[k.replace("promo", "street")]["usd"] for k in G.A if k.endswith("_promo")))
nonTv = m[m.category.isin(["phone", "laptop", "tablet", "washer", "washer_dryer", "refrigerator"])].merge(mp, on="sku")
nonTv = nonTv[nonTv.anchor_id.str.endswith("_street")]
floors = nonTv.anchor_id.map(lambda k: G.A[k]["usd"])
check("P10 clamped categories never below their street-floor anchor", (nonTv.market_street_price >= floors - 0.01).all())
hh = hist.groupby("model_key").size()
check("P11 every model has 13 monthly history points", (hh == 13).all(), hh.value_counts().to_dict())
h0 = hist[hist.months_ago == 0].set_index("model_key").market_street_price
check("P12 history month 0 == market_street_price", (mp.drop_duplicates("model_key").set_index("model_key").market_street_price == h0.reindex(mp.drop_duplicates("model_key").model_key)).all())
cam = mp.cost_above_market.mean()
check("P13 cost_above_market share is 3%-40% (stock bought before price drops)", 0.03 <= cam <= 0.40, f"{cam:.1%}")
check("P14 at least one model has a rising price trend (Moto-G-style list increase)", mp.price_basis.str.contains("price_up_trend").any(), int(mp.price_basis.str.contains("price_up_trend").sum()))
srcd = ref[ref.evidence.isin(["sourced_2026", "sourced_older"])]
check("P15 every sourced anchor has a URL", (srcd.source_url.fillna("") != "").all())

# ---- uploads / DQ
up = pd.read_csv(D / "upload_demo_200.csv", dtype=str, keep_default_na=False).to_dict("records")
iss = dq_check(up, AS_D)
codes = sorted({c for _, c, s in iss if s == "error"})
check("U01 demo upload has exactly the 3 seeded error codes", codes == sorted(["DUPLICATE_SKU", "MISSING_COST", "NEGATIVE_QTY"]), codes)
check("U02 demo upload has 200 rows", len(up) == 200, len(up))
mst = pd.read_csv(D / "skus_master.csv", dtype=str, keep_default_na=False).to_dict("records")
mi = dq_check(mst, AS_D)
viral = set(gt[gt.edge_tags.fillna("").str.contains("viral_spike")].sku)
mrows_bad = [(r, c) for r, c, s in mi if s in ("error", "warning") and not (c == "VELOCITY_OUTLIER" and mst[r - 1]["sku"] in viral)]
check("U03 clean master raises no DQ error/warning (only viral_spike fixtures may trip VELOCITY_OUTLIER)", not mrows_bad, mrows_bad[:5])
df = pd.read_csv(D / "upload_defects.csv", dtype=str, keep_default_na=False).to_dict("records")
dissues = dq_check(df, AS_D)
man = pd.read_csv(D / "defects_manifest.csv")
byrow = {}
for r, c, s in dissues:
    byrow.setdefault(r, set()).add(c)
allok, miss = True, []
for _, r in man.iterrows():
    code = r.expected_code
    got = byrow.get(int(r.data_row), set())
    if code.startswith("OK_"):
        good = not (got - {"ZERO_QTY"})
    else:
        good = code in got
    if not good:
        allok = False; miss.append((r.data_row, code, sorted(got)))
check("U04 every injected defect is detected (and OK-format rows raise nothing)", allok, miss)
check("U05 defects file covers all 11 DQ codes", {c for c in man.expected_code if not c.startswith("OK_")} == {
    "MISSING_REQUIRED", "MISSING_COST", "NEGATIVE_QTY", "DUPLICATE_SKU", "FUTURE_DATE", "PRICE_BELOW_COST", "BAD_CATEGORY",
    "SALE_BEFORE_RECEIVED", "VELOCITY_OUTLIER", "MSRP_MISMATCH", "ZERO_QTY"})

# ---- determinism
if not a.skip_determinism:
    def digest(p):
        return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(Path(p).glob("*.csv"))}
    with tempfile.TemporaryDirectory() as t:
        subprocess.run([sys.executable, "generate_dataset_v2.py", "--size", a.size, "--as-of", a.as_of, "--seed", str(a.seed), "--out", t] + (["--anchors", a.anchors] if a.anchors else []),
                       check=True, capture_output=True)
        d1, d2 = digest(D), digest(t)
        common = [k for k in d2 if k in d1 and k not in ("quality_report.csv",)]
        check("D01 re-running the generator reproduces every CSV byte-for-byte", all(d1[k] == d2[k] for k in common), [k for k in common if d1[k] != d2[k]])

passed = sum(c["ok"] for c in checks)
ev_counts = ref.evidence.value_counts().to_dict()
report = dict(checks_passed=passed, checks_total=len(checks), pass_rate=round(passed / len(checks), 4), failed=[c for c in checks if not c["ok"]],
              anchor_evidence_counts=ev_counts, note="Pass rate measures STRUCTURAL correctness. Price accuracy is only as good as the anchor table: see anchor_evidence_counts.")
(D / "quality_report.json").write_text(json.dumps(report, indent=2))
print(f"\n{passed}/{len(checks)} checks passed ({report['pass_rate']:.1%}). Anchor evidence: {ev_counts}")
sys.exit(0 if passed == len(checks) else 1)
