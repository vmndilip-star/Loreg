#!/usr/bin/env python3
"""Build snapshot.json: every number the Loreg UI shows, computed from the product database (CSV).

Deterministic code only. No model is involved. Inputs (same folder as --master unless given):
  skus_master.csv        the shop database in Loreg upload format
  ground_truth.csv       optional, gives launch_family flags (Samsung 2024 TV lines in the sample data)
  market_prices.csv      optional, gives estimated market price (for "your price is X% above market")
  summary.csv            optional, cross-check of dead/slow capital against the generator's own totals
Usage: python snapshot.py --master path/to/skus_master.csv --as-of 2026-10-26 --out snapshot.json
       [--upload other_upload.csv]   (data-check numbers come from this file; default: the master itself)
"""
import argparse, csv, json, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from loreg_rules import TH, classify, dq_check
except ImportError:  # run from the prior/ copy
    sys.path.insert(0, str(Path(__file__).parent / "prior" / "dataset_v2"))
    from loreg_rules import TH, classify, dq_check

LABEL = {"tv": "TVs", "phone": "Phones", "laptop": "Laptops", "tablet": "Tablets", "washer": "Washers",
         "washer_dryer": "Washer-dryers", "refrigerator": "Fridges", "accessory": "Accessories"}
SINGULAR = {"tv": "TV", "phone": "phone", "laptop": "laptop", "tablet": "tablet", "washer": "washer",
            "washer_dryer": "washer-dryer", "refrigerator": "fridge", "accessory": "accessory"}
LADDER = (0.05, 0.10, 0.15, 0.20)


def money(x):
    return "${:,.0f}".format(round(x))


def pdate(s):
    return date.fromisoformat(s) if s else None


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def plan_for(r, mk, as_of):
    """One option per item, from the Loreg rules. Prototype rule order: discount ladder, return, below-cost discount, bundle, hold."""
    cat = r["category"]
    slow_d, dead_d, slow_dos, _ = TH[cat]
    q, cost, sell = r["qty"], r["cost"], r["sell"]
    dsls, u90 = r["dsls"], r["u90"]
    dos = round(q / (u90 / 90)) if u90 > 0 else None
    market = mk.get(r["sku"])
    gap = (sell - market) / market if market else None
    chips = ["SKU " + r["sku"]]
    chips.append("Days of stock " + (str(dos) if dos is not None else "no sales in 90 days"))
    if market and cost > market:
        chips.append("Cost above market")
    elif market:
        chips.append("Market price: estimate")
    d_ok = next((d for d in LADDER if sell * (1 - d) >= cost), None)
    mkt_line = None
    if market:
        mkt_line = ("Your price is about {:.0f}% above the estimated market price of {}.".format(gap * 100, money(market))
                    if gap > 0.05 else "Your price is close to the estimated market price of {}.".format(money(market)))
    if d_ok is not None:
        profit = q * (sell * (1 - d_ok) - cost)
        given = q * sell * d_ok
        reasons = ["You still make {} across the {}. The discount gives up about {}.".format(money(profit), q, money(given))]
        if mkt_line:
            reasons.append(mkt_line)
        option = "Discount {:.0f}%".format(d_ok * 100)
    elif r["return_end"] and r["return_end"] >= as_of:
        reasons = ["The supplier return window is open until {}.".format(r["return_end"].strftime("%b %-d, %Y")),
                   "Returning {} items frees about {} in cash.".format(q, money(q * cost))]
        option = "Return"
    elif dsls is not None and dsls >= 2 * dead_d:
        d = 0.10
        loss = q * (cost - sell * (1 - d))
        reasons = ["No sale in {} days, past twice the dead limit for a {} ({}), so Loreg allows a price below cost.".format(dsls, SINGULAR[cat], 2 * dead_d),
                   "This sells below your cost: about {} lost, but about {} comes back in cash.".format(money(max(loss, 0)), money(q * sell * (1 - d)))]
        option = "Discount 10%"
    elif dos is None or dos > slow_dos:
        loss5 = q * (cost - sell * 0.95)
        reasons = ["A 5% discount would sell below your cost: about {} lost across the {}.".format(money(max(loss5, 0)), q),
                   ("{} days of stock at the current pace, against the {} Loreg allows for a {}.".format(dos, slow_dos, SINGULAR[cat])
                    if dos is not None else "No sales in the last 90 days.")]
        option = "Bundle"
    else:
        reasons = ["A discount would sell below your cost.",
                   "It is still selling: last sale {} days ago.".format(dsls)]
        option = "Hold"
    return {"id": r["sku"], "name": r["name"], "shelf": q, "tied": round(r["cap"]), "option": option,
            "reasons": reasons, "chips": chips + ["Source: [outlet], [date]"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", required=True)
    ap.add_argument("--as-of", default="2026-10-26")
    ap.add_argument("--out", default="snapshot.json")
    ap.add_argument("--upload", default=None)
    ap.add_argument("--shop", default="Sample shop")
    a = ap.parse_args()
    as_of = date.fromisoformat(a.as_of)
    folder = Path(a.master).parent

    rows = read(a.master)
    launch = {}
    if (folder / "ground_truth.csv").exists():
        launch = {g["sku"]: g["launch_family"] == "1" for g in read(folder / "ground_truth.csv")}
    mk = {}
    if (folder / "market_prices.csv").exists():
        mk = {m["sku"]: float(m["market_street_price"]) for m in read(folder / "market_prices.csv")}

    items = []
    for r in rows:
        qty = int(float(r["qty_on_hand"]))
        cost = float(r["unit_cost"]) if r["unit_cost"] else 0.0
        u90 = int(float(r["units_sold_90d"] or 0)) if "units_sold_90d" in r else int(float(r.get("units_sold_90d") or 0))
        last = pdate(r["last_sale_date"])
        recv = pdate(r["received_date"])
        st = classify(qty, recv, last, u90, r["category"], int(r["is_seasonal"] or 0), as_of)
        items.append(dict(sku=r["sku"], name=(r["brand"] + " " + r["model"]).strip(), category=r["category"], qty=qty, cost=cost,
                          sell=float(r["sell_price"]), u90=u90, dsls=(as_of - last).days if last else None, status=st,
                          cap=qty * cost, return_end=pdate(r.get("return_window_end", "")), launch=launch.get(r["sku"], False)))

    def tot(sel):
        s = [i for i in items if sel(i)]
        return {"n": len(s), "cap": round(sum(i["cap"] for i in s))}
    totals = {"skus": len(items), "stock_at_cost": round(sum(i["cap"] for i in items)),
              "dead": tot(lambda i: i["status"] == "dead"), "slow": tot(lambda i: i["status"] == "slow"),
              "at_risk": tot(lambda i: i["status"] in ("dead", "slow")), "new": tot(lambda i: i["status"] == "new"),
              "insufficient": tot(lambda i: i["status"] == "insufficient_data"), "zero_qty": sum(1 for i in items if i["qty"] == 0)}

    risk = [i for i in items if i["status"] in ("dead", "slow")]
    cats = {}
    for i in risk:
        c = cats.setdefault(i["category"], {"key": i["category"], "label": LABEL[i["category"]], "n": 0, "cap": 0.0})
        c["n"] += 1
        c["cap"] += i["cap"]
    by_cat = sorted(({**c, "cap": round(c["cap"])} for c in cats.values()), key=lambda c: -c["cap"])
    top_cap = by_cat[0]["cap"] if by_cat else 1
    for c in by_cat:
        c["pct"] = round(100 * c["cap"] / top_cap)

    def why(i):
        slow_d = TH[i["category"]][0]
        if i["dsls"] is not None and (i["status"] == "dead" or i["dsls"] >= slow_d):
            return "No sale in {} days".format(i["dsls"])
        return "Too much stock for its sales pace"
    top = sorted(risk, key=lambda i: -i["cap"])[:8]
    top_items = [{"name": i["name"], "category": SINGULAR[i["category"]].capitalize() if i["category"] != "tv" else "TV", "qty": i["qty"],
                  "dsls": i["dsls"], "status": i["status"], "why": why(i), "cap": round(i["cap"])} for i in top]

    exposed = sorted((i for i in risk if i["launch"]), key=lambda i: -i["cap"])
    launch_block = None
    if exposed:
        top3 = exposed[:3]
        brand = exposed[0]["name"].split()[0]
        launch_block = {"label": brand + " " + LABEL[exposed[0]["category"]], "brand": brand, "cat": LABEL[exposed[0]["category"]], "n": len(exposed),
                        "cap": round(sum(i["cap"] for i in exposed)),
                        "top": [{"name": i["name"], "shelf": i["qty"], "cap": round(i["cap"])} for i in top3],
                        "rest_n": len(exposed) - len(top3), "rest_cap": round(sum(i["cap"] for i in exposed[3:])),
                        "top3_cap": round(sum(i["cap"] for i in top3)),
                        "plans": [plan_for(i, mk, as_of) for i in top3]}

    # data check numbers come from the upload file
    up_path = a.upload or a.master
    up = read(up_path)
    issues = dq_check(up, as_of)
    err_rows = {r for r, c, s in issues if s == "error"}
    warn_rows = {r for r, c, s in issues if s == "warning"} - err_rows
    counts = {}
    for r, c, s in issues:
        counts[c] = counts.get(c, 0) + 1
    TEXT = {
        "MISSING_REQUIRED": "{n} item(s) are missing a required value. Fill it in or leave them out.",
        "MISSING_COST": "{n} item(s) have no cost, so we can’t tell how much money is tied up in them.",
        "NEGATIVE_QTY": "{n} item(s) show a negative quantity. Enter the real count.",
        "DUPLICATE_SKU": "{n} item(s) appear twice. Keep one row so they aren’t counted twice.",
        "FUTURE_DATE": "{n} item(s) have a date in the future. Check the date.",
        "PRICE_BELOW_COST": "{n} item(s) sell for less than you paid.",
        "BAD_CATEGORY": "{n} item(s) have a category Loreg doesn’t know.",
        "SALE_BEFORE_RECEIVED": "{n} item(s) show a sale before the stock arrived.",
        "VELOCITY_OUTLIER": "{n} item(s) sold unusually fast. Check the counts.",
        "MSRP_MISMATCH": "{n} item(s) are priced far above list price.",
    }
    sev = {"MISSING_REQUIRED": "error", "MISSING_COST": "error", "NEGATIVE_QTY": "error", "DUPLICATE_SKU": "error", "FUTURE_DATE": "error"}
    upload = {"file": Path(up_path).name, "rows": len(up), "ok": len(up) - len(err_rows) - len(warn_rows), "look": len(warn_rows),
              "blocked": len(err_rows), "zero_qty": counts.get("ZERO_QTY", 0),
              "issues": [{"code": c, "n": n, "level": sev.get(c, "warning"), "text": TEXT[c].format(n=n)} for c, n in counts.items() if c in TEXT]}

    # sample bill: the 4 largest at-risk items plus one with a partly unreadable name
    bl = sorted(risk, key=lambda i: -i["cap"])[:5]
    bill_lines = []
    for k, i in enumerate(bl):
        shown = i["name"].split(" ", 1)[1] if " " in i["name"] else i["name"]
        unclear = k == 4
        bill_lines.append({"on_bill": (shown[:-1] + "?") if unclear else shown, "qty": i["qty"], "each": round(i["cost"], 2),
                           "line": round(i["qty"] * i["cost"], 2), "matched": i["name"], "unclear": unclear})
    bill = {"lines": bill_lines, "sum": round(sum(l["line"] for l in bill_lines), 2)}

    snap = {"as_of": a.as_of, "shop": a.shop, "source": Path(a.master).name, "totals": totals, "by_category": by_cat,
            "top_items": top_items, "launch": launch_block, "upload": upload, "bill": bill}
    Path(a.out).write_text(json.dumps(snap, indent=1))

    # cross-check against the generator's own summary, when it is next to the master
    sm = folder / "summary.csv"
    if sm.exists():
        t = next(r for r in read(sm) if r["category"] == "TOTAL")
        ok = (int(t["dead_capital"]) == totals["dead"]["cap"] and int(t["slow_capital"]) == totals["slow"]["cap"]
              and int(t["skus"]) == totals["skus"])
        print("cross-check vs summary.csv:", "MATCH" if ok else "MISMATCH", t["dead_capital"], t["slow_capital"])
        if not ok:
            sys.exit(1)
    print(json.dumps({"skus": totals["skus"], "stock_at_cost": totals["stock_at_cost"], "dead": totals["dead"], "slow": totals["slow"],
                      "at_risk": totals["at_risk"], "launch": (launch_block or {}).get("n")}))


if __name__ == "__main__":
    main()
