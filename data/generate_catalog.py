#!/usr/bin/env python3
"""Generate the Loreg shop database: exactly 3,000 unique products from data/catalog_lines.yaml.

    python data/generate_catalog.py [--seed 2026] [--as-of 2026-10-26] [--out data/generated] [--anchors my_prices.csv]

Deterministic: the same arguments give byte-identical files. Standard library plus PyYAML.

Writes (Loreg upload format first):
  skus_master.csv            3,000 rows: sku, brand, model, category, qty_on_hand, unit_cost, sell_price,
                             last_sale_date, received_date, units_sold_30d, units_sold_90d, msrp, is_seasonal,
                             supplier, return_window_end
  sales_events.csv           12 months of sales and customer returns
  market_prices.csv          market price today per SKU, with provenance
  market_price_history.csv   13 monthly market prices per SKU
  ground_truth.csv           intended / expected / computed status, edge tags, line, launch family, fixture flag
  summary.csv                stock at cost, dead and slow capital per category, with and without fixture rows
  upload_demo_200.csv        200 rows taken from the 3,000 with exactly 3 seeded errors
  upload_defects.csv + defects_manifest.csv   one row per data-quality code plus 4 format cases that must parse

What is real and what is not: brand and product-line names are real product lines (unverified, see
data/names_to_verify.csv). Every price, cost, quantity and sale is SYNTHETIC. Prices come from the anchor
table data/market_anchors.csv: anchors are labelled sourced_2026, sourced_older or estimated, and most are
estimated. Nothing here is a real price.

Rules live in app/core and thresholds in config/thresholds.yaml; this script only builds data that those
rules then classify.
"""

import argparse
import csv
import math
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.catalog import CATEGORY_ORDER, expand_catalog, slug, storage_label  # noqa: E402,F401
from app.core.classify import DEAD, HEALTHY, INSUFFICIENT, NEW, SLOW, classify  # noqa: E402
from app.core.quality import dq_check  # noqa: E402
from app.core.thresholds import load_thresholds  # noqa: E402

DEPT = {"tv": "Displays", "phone": "Phones & Tablets", "laptop": "Computing",
        "washer": "Large Appliances", "washer_dryer": "Large Appliances", "refrigerator": "Large Appliances"}
SUBCAT = {"tv": "TVs", "phone": "Phones", "laptop": "Laptops", "washer": "Washers",
          "washer_dryer": "Washer-Dryers", "refrigerator": "Refrigerators"}
HEADER = ["sku", "brand", "model", "category", "qty_on_hand", "unit_cost", "sell_price", "last_sale_date",
          "received_date", "units_sold_30d", "units_sold_90d", "msrp", "is_seasonal", "supplier", "return_window_end"]
SUPPLIER = {"Apple": "Ingram Micro", "Samsung": "Samsung Direct", "LG": "LG Distribution", "Sony": "Sony Direct",
            "Google": "Ingram Micro", "Motorola": "TD Synnex", "OnePlus": "TD Synnex"}
ENTRY_PHONE_LINES = {"Moto G Play", "Galaxy A06", "Galaxy A14 5G", "Galaxy A15 5G", "OnePlus Nord N20 5G",
                     "OnePlus Nord N30 5G"}
TV_ANCHOR_SIZES = [43, 50, 55, 65, 75, 85]
RATE = {"tv": (-2.9, 0.6), "phone": (-1.9, 0.6), "laptop": (-3.2, 0.5), "washer": (-3.4, 0.5),
        "washer_dryer": (-3.6, 0.5), "refrigerator": (-3.6, 0.5)}
RATE_CAP = 0.35            # sales per day for natural sellers; stays well under the velocity-outlier floor
QTY_DOS = {"tv": (18, 60), "phone": (14, 55), "laptop": (20, 60), "washer": (25, 70), "washer_dryer": (25, 70),
           "refrigerator": (25, 70)}
COST_RATIO = {"tv": (0.86, 0.95), "phone": (0.90, 0.96), "laptop": (0.87, 0.94), "washer": (0.76, 0.87),
              "washer_dryer": (0.76, 0.87), "refrigerator": (0.76, 0.87)}
HIST_MONTHS = 12


def R(*key):
    """Deterministic RNG per key, independent of iteration order."""
    return random.Random("|".join(str(k) for k in key))


def psych(x):
    """Retail-style price: whole dollars minus a cent. Monotone in x."""
    if x < 10:
        return round(x * 100) / 100
    return max(int(round(x)) - 0.01, 0.99)


def poisson(rng, lam):
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def lerp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            t = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return ys[-1]


# ======================================================================================= anchors
ANCHOR_COLUMNS = ["anchor_id", "category", "segment", "price_type", "usd", "observed_on", "evidence", "source_url", "note"]


def load_anchors(path, override=None):
    anchors = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["usd"] = float(r["usd"])
            anchors[r["anchor_id"]] = r
    if override:
        with open(override, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["anchor_id"] not in anchors:
                    raise SystemExit(f"unknown anchor_id in --anchors: {r['anchor_id']}")
                anchors[r["anchor_id"]].update(usd=float(r["usd"]), observed_on=r["observed_on"], evidence=r["evidence"],
                                               source_url=r.get("source_url", ""), note=r.get("note", ""))
    return anchors


def tv_floor(anchors, size, kind):
    """Cheapest price for a TV size, interpolated between anchored sizes. kind: street or promo."""
    ys = [anchors[f"tv_{s}_{kind}"]["usd"] for s in TV_ANCHOR_SIZES]
    if size < TV_ANCHOR_SIZES[0]:
        return ys[0] * (size / TV_ANCHOR_SIZES[0]) ** 1.5
    if size > TV_ANCHOR_SIZES[-1]:
        return ys[-1] * (size / TV_ANCHOR_SIZES[-1]) ** 2.5
    return lerp(size, TV_ANCHOR_SIZES, ys)


# ======================================================================================= catalog
def finish_factor(finish):
    f = finish.lower()
    k = 1.0
    if "stainless" in f or "steel" in f:
        k *= 1.04
    if "black" in f:
        k *= 1.03
    if "fingerprint" in f or "printproof" in f or "printshield" in f:
        k *= 1.04
    if "matte" in f:
        k *= 1.03
    if "panel" in f:
        k *= 1.10
    return k


# ======================================================================================= market prices
TV_TIER = {"budget": (1.00, 1.30), "mainstream": (1.45, 2.20), "premium": (2.30, 3.60), "flagship": (3.70, 6.50)}
TV_GEN = {2025: 1.0, 2024: 0.94, 2023: 0.86, 2022: 0.78}
PHONE_GEN = {2025: 1.0, 2024: 0.90, 2023: 0.80, 2022: 0.70}
APPLE_GEN = {2025: 1.0, 2024: 0.94, 2023: 0.88, 2022: 0.82}
LAPTOP_GEN = {2025: 1.0, 2024: 0.93, 2023: 0.85, 2022: 0.78}
APPL_GEN = {2025: 1.0, 2024: 0.97, 2023: 0.93, 2022: 0.88}
APPL_TIER = {"budget": (1.00, 1.12), "mainstream": (1.15, 1.45), "premium": (1.50, 1.95), "flagship": (2.00, 2.60)}
FRIDGE_REF = {"top_freezer": 18.0, "side_by_side": 25.0, "french_door": 27.0, "bottom_freezer": 20.0, "compact": 3.3}


def fridge_segment(line):
    l = line.lower()
    for key, seg in (("compact", "compact"), ("top freezer", "top_freezer"), ("side-by-side", "side_by_side"),
                     ("bottom freezer", "bottom_freezer")):
        if key in l:
            return seg
    return "french_door"


def line_draws(seed, v):
    r = R(seed, "line", v["line_id"])
    return dict(tier_u=r.random(), clamp_u=r.random(), msrp_u=r.random(), promo_u=r.random(), noise_u=r.random())


def market_for(v, anchors, seed, as_of):
    """Deterministic market scenario for a variant: street price today, current promo floor, MSRP, history."""
    cat, d = v["category"], line_draws(seed, v)
    basis, anchor_id, size_note = "", "", ""
    if cat == "tv":
        size = v["size"]
        lo, hi = TV_TIER[v["tier"]]
        tier_mult = lo + d["tier_u"] * (hi - lo)
        size_boost = (size / 65.0) ** (0.35 if v["tier"] in ("premium", "flagship") else 0.0)
        gen = TV_GEN[v["year"]] ** (0.5 if v["tier"] == "budget" else 1.0)
        sf, pf = tv_floor(anchors, size, "street"), tv_floor(anchors, size, "promo")
        street = max(sf * tier_mult * size_boost * gen, sf * (1.0 + 0.08 * d["clamp_u"]))
        promo = max(pf, street * (0.80 + 0.13 * d["promo_u"]))
        msrp_mult = 1.12 + 0.43 * d["msrp_u"]
        basis = "anchor_size_tier"
        if v["leader"]:
            street, promo, basis = sf, pf, "anchor_size_tier+cheapest_line"
        near = min(TV_ANCHOR_SIZES, key=lambda s: abs(s - size))
        anchor_id = f"tv_{near}_street"
        decay = (0.004 if v["year"] >= 2025 else 0.012 if v["year"] == 2024 else 0.015) + (0.004 if v["tier"] != "budget" else 0.0)
        exact = v["leader"]
        floor_street, floor_promo = round(sf, 2), round(pf, 2)
    else:
        if cat == "phone":
            seg = "entry" if (v["tier"] == "budget" and v["line"] in ENTRY_PHONE_LINES) else v["tier"]
            gen = (APPLE_GEN if v["brand"] == "Apple" else PHONE_GEN)[v["year"]]
            sizef = max(0.8, 1 + 0.14 * math.log2(v["size"] / 128.0))
            noise = 1.0 + 0.30 * d["noise_u"]
            sid, decay = f"phone_{seg}", (0.007 if v["brand"] == "Apple" else 0.018 if v["tier"] == "flagship" else 0.006)
            msrp_mult = 1.0 + 0.25 * d["msrp_u"]
        elif cat == "laptop":
            seg = "entry" if v["tier"] == "budget" else v["tier"]
            gen = LAPTOP_GEN[v["year"]]
            ram, ssd = v["size"]
            sizef = max(0.7, (1 + 0.15 * math.log2(ram / 8.0)) * (1 + 0.08 * math.log2(ssd / 256.0)))
            noise = 1.0 + 0.30 * d["noise_u"]
            sid, decay = f"laptop_{seg}", (0.007 if v["brand"] == "Apple" else 0.008)
            msrp_mult = 1.0 + 0.25 * d["msrp_u"]
        else:
            lo, hi = APPL_TIER[v["tier"]]
            noise = lo + d["tier_u"] * (hi - lo)
            gen = APPL_GEN[v["year"]]
            decay = 0.003
            msrp_mult = 1.10 + 0.30 * d["msrp_u"]
            cap, fin = v["size"], finish_factor(v["dims"]["finish"])
            if cat == "washer":
                seg = "top" if "top load" in v["line"].lower() else "front"
                sid, sizef = f"washer_{'top' if seg == 'top' else 'front'}", (cap / 4.5) ** 0.6 * fin
            elif cat == "washer_dryer":
                seg, sid, sizef = "combo", "washer_dryer", (cap / 4.5) ** 0.5 * fin
            else:
                seg = fridge_segment(v["line"])
                sid = {"top_freezer": "fridge_top", "side_by_side": "fridge_side", "french_door": "fridge_french",
                       "bottom_freezer": "fridge_bottom", "compact": "fridge_compact"}[seg]
                sizef = (cap / FRIDGE_REF[seg]) ** 0.7 * fin
        sa, pa = anchors[f"{sid}_street"], anchors[f"{sid}_promo"]
        floor_street, floor_promo = sa["usd"], pa["usd"]
        street = max(sa["usd"] * noise * gen * sizef, sa["usd"] * (1.0 + 0.06 * d["clamp_u"]))
        promo = max(pa["usd"], street * (0.80 + 0.13 * d["promo_u"]))
        basis, anchor_id = "segment_anchor_tier", f"{sid}_street"
        exact = False
        if v["price_up"]:
            decay, basis = -0.015, basis + "+price_up_trend"
    if exact:
        street_p, promo_p = floor_street, floor_promo
    else:
        street_p = max(psych(max(street, promo * 1.02)), floor_street)   # never below the cheapest regular price
        promo_p = min(max(psych(max(promo, 1.0)), floor_promo), street_p)
    msrp = psych(max(street_p * msrp_mult, street_p * 1.02))
    hist = [psych(street_p * (1 + decay) ** m) for m in range(HIST_MONTHS + 1)]
    hist[0] = street_p
    return dict(street=street_p, promo=promo_p, msrp=msrp, basis=basis, anchor=anchor_id,
                evidence=anchors[anchor_id]["evidence"], hist=hist, decay=decay)


# ======================================================================================= fixtures
FIXTURE_PLAN = [
    ("tv", "never_sold_new"), ("tv", "never_sold_old"), ("tv", "dsls_equals_slow_threshold"),
    ("tv", "dsls_equals_dead_threshold"), ("tv", "launch_family"), ("tv", "seasonal_past_dead_threshold"),
    ("tv", "viral_spike"), ("tv", "dead_return_window_open"),
    ("phone", "never_sold_mid_age"), ("phone", "no_received_never_sold"), ("phone", "dsls_slow_minus_1"),
    ("phone", "dsls_dead_minus_1"), ("phone", "price_up_trend"), ("phone", "overpriced_vs_market"),
    ("phone", "dsls_equals_dead_threshold"), ("phone", "dead_return_window_closed"),
    ("laptop", "no_received_but_sold_slow"), ("laptop", "stockout_recent_sale"), ("laptop", "stockout_dead_history"),
    ("laptop", "sold_today_overstock"), ("laptop", "cost_above_market_dead"), ("laptop", "seasonal_past_slow_threshold"),
    ("laptop", "dsls_equals_slow_threshold"),
    ("washer", "never_sold_new"), ("washer", "never_sold_old"), ("washer", "dsls_equals_dead_threshold"),
    ("washer", "dsls_dead_minus_1"), ("washer", "dead_return_window_open"),
    ("washer_dryer", "never_sold_mid_age"), ("washer_dryer", "dsls_equals_slow_threshold"),
    ("washer_dryer", "sold_today_overstock"),
    ("refrigerator", "never_sold_old"), ("refrigerator", "dsls_slow_minus_1"),
    ("refrigerator", "dsls_equals_dead_threshold"), ("refrigerator", "cost_above_market_dead"),
]


def fixture_spec(tag, t):
    """Hand-labeled fixture: what the row looks like and the status a person would give it. Independent of the classifier."""
    s, dd, dos, g = int(t.slow_dsls), int(t.dead_dsls), int(t.slow_days_of_stock), int(t.new_grace_days)
    table = {
        "never_sold_new": (dict(age=g - 5, dsls=None, qty=6), NEW),
        "never_sold_old": (dict(age=dd + 60, dsls=None, qty=4), DEAD),
        "never_sold_mid_age": (dict(age=(g + dd) // 2, dsls=None, qty=4), INSUFFICIENT),
        "no_received_never_sold": (dict(age=None, dsls=None, qty=3), INSUFFICIENT),
        "no_received_but_sold_slow": (dict(age=None, dsls=s + 5, qty=3), SLOW),
        "stockout_recent_sale": (dict(age=200, dsls=2, qty=0), HEALTHY),
        "stockout_dead_history": (dict(age=300, dsls=dd + 30, qty=0), HEALTHY),
        "sold_today_overstock": (dict(age=200, dsls=0, qty=12, u90=1, qmin=2), SLOW),
        "dsls_equals_slow_threshold": (dict(age=s + 60, dsls=s, qty=3), SLOW),
        "dsls_slow_minus_1": (dict(age=s + 60, dsls=s - 1, qty=1, u90=1), HEALTHY),
        "dsls_equals_dead_threshold": (dict(age=dd + 60, dsls=dd, qty=3), DEAD),
        "dsls_dead_minus_1": (dict(age=dd + 60, dsls=dd - 1, qty=3), SLOW),
        "seasonal_past_dead_threshold": (dict(age=dd + 120, dsls=int(dd * 1.2), qty=3, seasonal=1), SLOW),
        "seasonal_past_slow_threshold": (dict(age=s + 60, dsls=int(s * 1.1), qty=1, u90=1, seasonal=1), HEALTHY),
        "overpriced_vs_market": (dict(age=s + 80, dsls=s + 10, qty=4, gap=0.22), SLOW),
        "cost_above_market_dead": (dict(age=dd + 90, dsls=dd + 40, qty=5, cost_mult=1.22), DEAD),
        "viral_spike": (dict(age=120, dsls=1, qty=5, spike=True), HEALTHY),
        "dead_return_window_open": (dict(age=dd + 20, dsls=dd + 10, qty=4, return_open=True), DEAD),
        "dead_return_window_closed": (dict(age=dd + 50, dsls=dd + 30, qty=4, return_open=False), DEAD),
        "launch_family": (dict(age=100, dsls=3, qty=3, u90=2, qmin=3), SLOW),
        "price_up_trend": (dict(age=200, dsls=5, qty=2, u90=6), HEALTHY),
    }
    spec, expected = table[tag]
    return dict(spec, tag=tag), expected


def pick_fixture_items(variants, anchors, seed, as_of, th):
    """One variant per fixture, chosen so qty x cost stays under the cap and the hand label holds."""
    cap = th.generator["fixture_capital_cap_usd"]
    taken, picks = set(), []
    market = {}
    for cat, tag in FIXTURE_PLAN:
        spec, expected = fixture_spec(tag, th.categories[cat])
        qmin = spec.get("qmin", 1 if spec["qty"] > 0 else 0)
        cands = [v for v in variants if v["category"] == cat and v["sku"] not in taken]
        if tag == "launch_family":
            cands = [v for v in cands if v["launch"]]
        elif tag == "price_up_trend":
            cands = [v for v in cands if v["price_up"]]
        else:
            cands = [v for v in cands if not v["launch"] and not v["price_up"] and not v["leader"]]
        R(seed, "fixture-order", cat, tag).shuffle(cands)
        chosen = None
        for v in cands:
            m = market.setdefault(v["sku"], market_for(v, anchors, seed, as_of))
            worst_cost = max(m["hist"]) * 0.96 * spec.get("cost_mult", 1.0)
            if qmin * worst_cost <= cap:
                chosen = v
                break
        if chosen is None:
            raise SystemExit(f"no variant fits fixture {cat}/{tag} under the ${cap} cap")
        taken.add(chosen["sku"])
        picks.append((chosen, spec, expected))
    return picks


# ======================================================================================= sales events
def day_mult(d, cat):
    m = 1.25 if d.weekday() >= 5 else 1.0
    big = cat in ("tv", "laptop", "washer", "washer_dryer", "refrigerator")
    if date(2025, 11, 28) <= d <= date(2025, 12, 1):
        m *= 2.5 if big else 1.8
    elif date(2025, 12, 15) <= d <= date(2025, 12, 24):
        m *= 1.4
    return m


def simulate_sales(rng, received, last_sale, rate, cat, as_of):
    """Poisson daily sales from received (or 12 months ago) to the last sale; the last day always has a sale."""
    if last_sale is None:
        return []
    start = max(received or (as_of - timedelta(days=364)), as_of - timedelta(days=364))
    end = max(last_sale, start)
    out, d = [], start
    while d <= end:
        c = poisson(rng, rate * day_mult(d, cat))
        if d == end:
            c = max(c, 1)
        if c:
            out.append((d, c))
        d += timedelta(days=1)
    return out


def fixture_sales(rng, spec, received, last_sale, as_of, u90_default):
    """Explicit sale days for a fixture so u90 and the last sale are exactly what the hand label needs."""
    if last_sale is None:
        return []
    dsls = (as_of - last_sale).days
    lo = max(received or (as_of - timedelta(days=364)), as_of - timedelta(days=364))
    window = (as_of - lo).days
    if spec.get("spike"):
        return [(as_of - timedelta(days=k), 3) for k in range(dsls, dsls + 15)]  # 45 units in 15 days
    out = [(last_sale, 1)]
    if dsls < 90:
        for _ in range((spec.get("u90") or u90_default) - 1):
            out.append((as_of - timedelta(days=rng.randint(dsls, min(89, window))), 1))
    first_old = max(90, dsls + 1)  # older sales: outside the 90-day window and never later than the last sale
    if window >= first_old:
        for _ in range(rng.randint(2, 5)):
            out.append((as_of - timedelta(days=rng.randint(first_old, window)), 1))
    merged = {}
    for d, c in out:
        merged[d] = merged.get(d, 0) + c
    return sorted(merged.items())


# ======================================================================================= SKU construction
def build_sku(v, m, status, spec, seed, as_of, th, anchors):
    cat = v["category"]
    t = th.categories[cat]
    r = R(seed, "sku", v["sku"])
    fixed = spec is not None
    seasonal = int(spec.get("seasonal", 0)) if fixed else 0
    st = th.for_category(cat, bool(seasonal))
    slow_d, dead_d, slow_dos, grace = int(st.slow_dsls), int(st.dead_dsls), int(st.slow_days_of_stock), int(st.new_grace_days)
    slow_type = None
    if fixed:
        age, dsls, qty = spec["age"], spec["dsls"], spec["qty"]
    elif status == DEAD:
        age = r.randint(dead_d + 30, 364)
        dsls = r.randint(dead_d, min(age - 5, dead_d + 170))
        qty = r.randint(1, 5 if cat in ("tv", "phone") else 3)
    elif status == SLOW:
        age = r.randint(slow_d + 40, 360)
        slow_type = "stopped" if r.random() < 0.5 else "overstock"
        if slow_type == "stopped":
            dsls = r.randint(slow_d, min(dead_d - 1, age - 5))
            qty = r.randint(2, 9 if cat in ("tv", "phone") else 5)
        else:
            dsls, qty = r.randint(2, 30), 3
    elif status == NEW:
        age = r.randint(3, grace - 1)
        dsls = None if r.random() < 0.6 else r.randint(0, age)
        qty = r.randint(3, 12)
    else:  # healthy
        age = r.randint(max(45, grace + 5), 364)
        dsls = r.randint(0, min(22, age - 1))
        qty = 1
        if r.random() < 0.04:
            qty = 0  # stocked out
    mu, sg = RATE[cat]
    rate = min(math.exp(r.gauss(mu, sg)), RATE_CAP)
    if status == DEAD:
        rate *= 0.7
    if slow_type == "overstock":
        rate = r.uniform(0.008, 0.045)
    received = None if age is None else as_of - timedelta(days=age)
    last_sale = None if dsls is None else as_of - timedelta(days=dsls)

    months_bought = 6 if age is None else min(HIST_MONTHS, age // 30)
    cost_mult = spec.get("cost_mult", 1.0) if fixed else 1.0
    cost = m["hist"][months_bought] * r.uniform(*COST_RATIO[cat]) * cost_mult
    cost = round(min(cost, m["msrp"] * 0.97), 2)
    gap = spec.get("gap") if fixed and spec.get("gap") else r.uniform(-0.03, 0.06)
    if not fixed and status in (DEAD, SLOW) and r.random() < 0.30:
        gap = r.uniform(0.10, 0.22)  # price held above market: a common reason stock stalls
    sell = min(round(m["street"] * (1 + gap), 2), round(m["msrp"] * 1.15, 2))
    sell = max(sell, round(cost * 1.02, 2))

    if fixed:
        events = fixture_sales(r, spec, received, last_sale, as_of, r.randint(1, 3))
    else:
        events = [(d, c) for d, c in simulate_sales(r, received, last_sale, rate, cat, as_of)]
    u30 = sum(c for d, c in events if (as_of - d).days < 30)
    u90 = sum(c for d, c in events if (as_of - d).days < 90)

    if not fixed:
        v90 = max(u90, 1) / 90.0
        lo_d, hi_d = QTY_DOS[cat]
        if status == HEALTHY and qty > 0:
            q = max(1, min(60, int(v90 * r.uniform(lo_d, min(hi_d, slow_dos * 0.8)))))
            while q / v90 > slow_dos * 0.9 and q > 1:
                q -= 1
            qty = q
        elif status == SLOW and slow_type == "overstock":
            qty = min(60, int(v90 * r.uniform(slow_dos + 12, slow_dos + 70)) + 1)
    else:
        cap_qty = int(th.generator["fixture_capital_cap_usd"] // cost)
        qty = 0 if qty == 0 else max(1, min(qty, cap_qty))

    rw = None
    if fixed and spec.get("return_open") is not None:
        rw = as_of + timedelta(days=10) if spec["return_open"] else (received or as_of) + timedelta(days=60)
        if not spec["return_open"]:
            rw = min(rw, as_of - timedelta(days=1))
    elif received:
        rw = received + timedelta(days=r.choice([30, 45, 60, 90]))
    returns = []
    if not fixed:
        for d, c in events:
            k = sum(1 for _ in range(c) if r.random() < 0.015)
            if k:
                returns.append((min(d + timedelta(days=r.randint(2, 14)), as_of), k))
    return dict(v, status=status, fixed=fixed, spec=spec, cost=cost, sell=sell, qty=qty, received=received,
                last_sale=last_sale, u30=u30, u90=u90, seasonal=seasonal, mp=m, events=events, returns=returns,
                return_end=(rw if rw and rw >= as_of else None), slow_type=slow_type,
                supplier=SUPPLIER.get(v["brand"], "Regional Distributor"))


def plan_statuses(variants_in_cat, fixture_statuses, th, seed, cat):
    """Exact status quotas per category (the rest are healthy); launch-family stock leans dead or slow."""
    n = len(variants_in_cat)
    mix = th.generator["status_mix"]
    quota = {DEAD: round(mix["dead"] * n), SLOW: round(mix["slow"] * n), NEW: round(mix["new"] * n)}
    for s in fixture_statuses.values():
        if s in quota:
            quota[s] -= 1
    out, rest = {}, []
    for v in variants_in_cat:
        if v["sku"] in fixture_statuses:
            continue
        if v["launch"]:
            x = R(seed, "launch", v["sku"]).random()
            s = DEAD if x < 0.4 else SLOW if x < 0.75 else HEALTHY
            out[v["sku"]] = s
            if s in quota:
                quota[s] -= 1
        else:
            rest.append(v["sku"])
    pool = [DEAD] * max(quota[DEAD], 0) + [SLOW] * max(quota[SLOW], 0) + [NEW] * max(quota[NEW], 0)
    pool += [HEALTHY] * (len(rest) - len(pool))
    pool = pool[:len(rest)]
    R(seed, "status-shuffle", cat).shuffle(pool)
    out.update(zip(rest, pool))
    return out


# ======================================================================================= output
def money(x):
    return f"{x:.2f}"


def size_label(s):
    sz = s["size"]
    if s["category"] == "laptop":
        return s["dims"]["config"]
    return f"{sz}" if s["category"] != "phone" else storage_label(sz)


def master_row(s):
    return [s["sku"], s["brand"], s["model"], s["category"], s["qty"], money(s["cost"]), money(s["sell"]),
            s["last_sale"].isoformat() if s["last_sale"] else "", s["received"].isoformat() if s["received"] else "",
            s["u30"], s["u90"], money(s["mp"]["msrp"]), s["seasonal"], s["supplier"],
            s["return_end"].isoformat() if s["return_end"] else ""]


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def share(a, b):
    return f"{a / b:.1%}" if b else "0.0%"


def build_demo_upload(skus, seed):
    """200 rows from the database: 24 Samsung launch-family TVs, 56 other TVs, 62 phones, 25 laptops, 20 washers,
    12 washer-dryers (199 unique) plus 1 duplicate; 3 seeded errors: missing cost, negative quantity, duplicate SKU."""
    rr = R(seed, "demo")
    ok = [s for s in skus if not s["fixed"] and s["category"] != "refrigerator"]
    launch = [s for s in ok if s["launch"]]
    launch.sort(key=lambda s: (s["status"] not in (DEAD, SLOW), s["sku"]))  # at-risk launch stock first
    chosen = launch[:24]
    others = {c: [s for s in ok if s["category"] == c and not s["launch"]] for c in CATEGORY_ORDER}
    for lst in others.values():
        rr.shuffle(lst)
    chosen += others["tv"][:56] + others["phone"][:62] + others["laptop"][:25] + others["washer"][:20] + others["washer_dryer"][:12]
    assert len(chosen) == 199, len(chosen)
    rows = [master_row(s) for s in chosen]
    rr.shuffle(rows)
    ix = {h: i for i, h in enumerate(HEADER)}
    rows[7][ix["unit_cost"]] = ""            # MISSING_COST
    rows[41][ix["qty_on_hand"]] = -2         # NEGATIVE_QTY
    rows.append(list(rows[93]))              # DUPLICATE_SKU (row 200)
    return rows


def build_defects(skus, as_of):
    ix = {h: i for i, h in enumerate(HEADER)}
    clean = [master_row(s) for s in skus if not s["fixed"] and s["category"] in ("tv", "phone", "laptop")
             and s["status"] in (HEALTHY, SLOW, DEAD) and s["qty"] > 0][:100]
    data, man = [list(r) for r in clean[:60]], []

    def inject(code, fn, note=""):
        row = list(clean[60 + len(man)])
        fn(row)
        data.append(row)
        man.append([len(data), code, note])

    inject("MISSING_REQUIRED", lambda r: r.__setitem__(ix["brand"], ""))
    inject("MISSING_COST", lambda r: r.__setitem__(ix["unit_cost"], ""))
    inject("NEGATIVE_QTY", lambda r: r.__setitem__(ix["qty_on_hand"], -3))
    data.append(list(data[2]))
    man.append([len(data), "DUPLICATE_SKU", "exact duplicate of row 3"])
    inject("FUTURE_DATE", lambda r: r.__setitem__(ix["last_sale_date"], (as_of + timedelta(days=9)).isoformat()))
    inject("PRICE_BELOW_COST", lambda r: r.__setitem__(ix["sell_price"], money(float(r[ix["unit_cost"]]) * 0.8)))
    inject("BAD_CATEGORY", lambda r: r.__setitem__(ix["category"], "televison"))
    inject("SALE_BEFORE_RECEIVED", lambda r: (r.__setitem__(ix["received_date"], (as_of - timedelta(days=10)).isoformat()),
                                              r.__setitem__(ix["last_sale_date"], (as_of - timedelta(days=40)).isoformat())))
    inject("VELOCITY_OUTLIER", lambda r: r.__setitem__(ix["units_sold_30d"], 999))
    inject("MSRP_MISMATCH", lambda r: r.__setitem__(ix["sell_price"], money(float(r[ix["msrp"]]) * 1.5)))
    inject("ZERO_QTY", lambda r: r.__setitem__(ix["qty_on_hand"], 0), "info only")
    inject("OK_US_DATE_FORMAT", lambda r: r.__setitem__(
        ix["last_sale_date"], date.fromisoformat(r[ix["last_sale_date"]]).strftime("%m/%d/%Y") if r[ix["last_sale_date"]] else ""),
        "must parse")
    inject("OK_CURRENCY_STRING", lambda r: r.__setitem__(ix["unit_cost"], f'${float(r[ix["unit_cost"]]):,.2f}'), "must parse")
    inject("OK_CATEGORY_SYNONYM", lambda r: r.__setitem__(ix["category"], "Television"), "must map to tv")
    inject("OK_FLOAT_QTY", lambda r: r.__setitem__(ix["qty_on_hand"], f'{float(r[ix["qty_on_hand"]]):.1f}'), "must parse")
    return data, man


def generate(args):
    th = load_thresholds(args.config)
    as_of = date.fromisoformat(args.as_of)
    seed = args.seed
    anchors = load_anchors(args.market_anchors, args.anchors)
    variants = expand_catalog(args.lines)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    market = {v["sku"]: market_for(v, anchors, seed, as_of) for v in variants}
    fixtures = {v["sku"]: (spec, exp) for v, spec, exp in pick_fixture_items(variants, anchors, seed, as_of, th)}

    skus = []
    for cat in CATEGORY_ORDER:
        in_cat = [v for v in variants if v["category"] == cat]
        fx_status = {sku: fixtures[sku][1] for sku in (v["sku"] for v in in_cat) if sku in fixtures}
        plan = plan_statuses(in_cat, fx_status, th, seed, cat)
        for v in in_cat:
            if v["sku"] in fixtures:
                spec, exp = fixtures[v["sku"]]
                s = build_sku(v, market[v["sku"]], exp if exp in (DEAD, SLOW, NEW) else HEALTHY, spec, seed, as_of, th, anchors)
                s["expected"], s["tag"] = exp, spec["tag"]
            else:
                s = build_sku(v, market[v["sku"]], plan[v["sku"]], None, seed, as_of, th, anchors)
                s["expected"], s["tag"] = plan[v["sku"]], ""
            s["intended"] = s["expected"]
            s["computed"] = classify(s["qty"], s["received"], s["last_sale"], s["u90"], cat, bool(s["seasonal"]), as_of, th)
            if s["computed"] != s["expected"]:
                raise AssertionError(f"{s['sku']} ({s['tag'] or s['status']}): built as {s['expected']}, classified {s['computed']}")
            skus.append(s)

    write_csv(out / "skus_master.csv", HEADER, [master_row(s) for s in skus])
    sales = []
    for s in skus:
        r = R(seed, "price-events", s["sku"])
        for d, c in s["events"]:
            unit = s["sell"] * (0.90 if date(2025, 11, 28) <= d <= date(2025, 12, 1) else r.uniform(0.95, 1.02))
            sales.append((s["sku"], d, c, round(max(unit, 0.5), 2), "sale"))
        for d, c in s["returns"]:
            sales.append((s["sku"], d, c, round(s["sell"], 2), "return"))
    write_csv(out / "sales_events.csv", ["sku", "event_date", "qty", "unit_price", "event_type"],
              [[k, d.isoformat(), c, money(p), t] for k, d, c, p, t in sorted(sales, key=lambda x: (x[1], x[0], x[4]))])

    write_csv(out / "market_prices.csv",
              ["sku", "line_id", "size_label", "market_street_price", "market_floor_price_today", "msrp_market",
               "price_gap_pct", "cost_above_market", "market_margin_at_cost_pct", "price_basis", "anchor_id", "price_evidence"],
              [[s["sku"], s["line_id"], size_label(s), money(s["mp"]["street"]), money(s["mp"]["promo"]), money(s["mp"]["msrp"]),
                f'{(s["sell"] - s["mp"]["street"]) / s["mp"]["street"]:.4f}', int(s["cost"] > s["mp"]["street"]),
                f'{(s["mp"]["street"] - s["cost"]) / s["mp"]["street"]:.4f}', s["mp"]["basis"], s["mp"]["anchor"],
                s["mp"]["evidence"]] for s in skus])
    write_csv(out / "market_price_history.csv", ["sku", "months_ago", "month_date", "market_street_price"],
              [[s["sku"], m, (as_of - timedelta(days=30 * m)).isoformat(), money(p)] for s in skus for m, p in enumerate(s["mp"]["hist"])])

    gt = []
    for s in skus:
        tags = [s["tag"]] if s["tag"] else []
        if s["cost"] > s["mp"]["street"]:
            tags.append("cost_above_market")
        if s["sell"] > s["mp"]["street"] * 1.10:
            tags.append("overpriced_vs_market")
        if s["launch"]:
            tags.append("launch_family")
        if s["return_end"] and s["computed"] == DEAD:
            tags.append("dead_return_open")
        if s["price_up"]:
            tags.append("price_up_trend")
        if s["seasonal"]:
            tags.append("seasonal")
        if s["qty"] == 0:
            tags.append("zero_qty")
        gt.append([s["sku"], s["line_id"], s["intended"], s["expected"], s["computed"], ";".join(dict.fromkeys(tags)), s["family"],
                   int(s["launch"]), DEPT[s["category"]], s["category"], SUBCAT[s["category"]], money(s["qty"] * s["cost"]), int(s["fixed"])])
    write_csv(out / "ground_truth.csv", ["sku", "line_id", "intended_status", "expected_status", "computed_status", "edge_tags", "family",
                                         "launch_family", "department", "category", "subcategory", "capital_at_cost", "is_fixture"], gt)

    def agg(rows):
        a = dict(n=0, cost=0.0, dn=0, dead=0.0, sn=0, slow=0.0)
        for s in rows:
            v = s["qty"] * s["cost"]
            a["n"] += 1
            a["cost"] += v
            if s["computed"] == DEAD:
                a["dn"] += 1
                a["dead"] += v
            elif s["computed"] == SLOW:
                a["sn"] += 1
                a["slow"] += v
        return a

    def summ_row(label, rows):
        a, b = agg(rows), agg([s for s in rows if not s["fixed"]])
        return [label, a["n"], round(a["cost"]), a["dn"], round(a["dead"]), share(a["dead"], a["cost"]), a["sn"], round(a["slow"]),
                share(a["slow"], a["cost"]), b["n"], round(b["cost"]), round(b["dead"]), share(b["dead"], b["cost"]),
                round(b["slow"]), share(b["slow"], b["cost"])]
    write_csv(out / "summary.csv",
              ["category", "skus", "stock_at_cost", "dead_skus", "dead_capital", "dead_pct", "slow_skus", "slow_capital", "slow_pct",
               "skus_ex_fixtures", "stock_at_cost_ex_fixtures", "dead_capital_ex_fixtures", "dead_pct_ex_fixtures",
               "slow_capital_ex_fixtures", "slow_pct_ex_fixtures"],
              [summ_row(c, [s for s in skus if s["category"] == c]) for c in CATEGORY_ORDER] + [summ_row("TOTAL", skus)])

    write_csv(out / "upload_demo_200.csv", HEADER, build_demo_upload(skus, seed))
    defects, manifest = build_defects(skus, as_of)
    write_csv(out / "upload_defects.csv", HEADER, defects)
    write_csv(out / "defects_manifest.csv", ["data_row", "expected_code", "note"], manifest)

    # sanity: the clean database must raise no errors
    rows = [dict(zip(HEADER, r)) for r in (master_row(s) for s in skus)]
    errs = [i for i in dq_check(rows, as_of, th) if i.severity == "error"]
    if errs:
        raise AssertionError(f"clean database raises data-quality errors: {errs[:5]}")
    return skus


def update_names_to_verify(skus, out, names_path):
    """Add a priority column: high for the launch family, the demo upload rows and the biggest at-risk items."""
    demo_skus = {row["sku"] for row in csv.DictReader(open(Path(out) / "upload_demo_200.csv", newline="", encoding="utf-8"))}
    by_sku = {s["sku"]: s for s in skus}
    risk = sorted((s for s in skus if s["computed"] in (DEAD, SLOW)), key=lambda s: (-s["qty"] * s["cost"], s["sku"]))[:40]
    high = {s["line_id"] for s in skus if s["launch"]} | {by_sku[k]["line_id"] for k in demo_skus if k in by_sku} | {s["line_id"] for s in risk}
    with open(names_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fields = [c for c in rows[0].keys() if c != "priority"]
    at = fields.index("name_confidence") + 1
    fields.insert(at, "priority")
    for r in rows:
        r["priority"] = "high" if r["line_id"] in high else "normal"
    with open(names_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return sum(1 for r in rows if r["priority"] == "high"), len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--as-of", default="2026-10-26")
    ap.add_argument("--out", default=str(REPO / "data" / "generated"))
    ap.add_argument("--anchors", default=None, help="CSV of anchor overrides (same columns as data/market_anchors.csv)")
    ap.add_argument("--lines", default=str(REPO / "data" / "catalog_lines.yaml"))
    ap.add_argument("--market-anchors", default=str(REPO / "data" / "market_anchors.csv"))
    ap.add_argument("--config", default=None, help="thresholds file (default config/thresholds.yaml)")
    ap.add_argument("--update-names-to-verify", action="store_true", help="add the priority column to data/names_to_verify.csv")
    a = ap.parse_args(argv)
    skus = generate(a)
    print(open(Path(a.out) / "summary.csv", encoding="utf-8").read())
    if a.update_names_to_verify:
        hi, n = update_names_to_verify(skus, a.out, REPO / "data" / "names_to_verify.csv")
        print(f"names_to_verify.csv: {hi} of {n} lines marked high priority")


if __name__ == "__main__":
    main()
