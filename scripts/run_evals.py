"""Run the eval set (evals/cases.yaml) against the rules and the recommender on a backend; print a results table.

    python scripts/run_evals.py                     (backend from LLM_BACKEND, default mock)
    python scripts/run_evals.py --backend meta --json evals/results_meta.json

A case passes when (1) the rules give exactly the expected status, allowed options, largest discount and flags, and (2) the
recommender's proposal (model answer, retried answer or rules fallback) is one of the allowed options, with a discount from the
allowed steps, and it is "proposed". The fallback rate counts cases where the model's answer was replaced by the rules; on mock no
model is asked. Every failure is printed; nothing is hidden or retried here.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASES = ROOT / "evals" / "cases.yaml"


def build_row(case_id: str, item: dict, as_of: date, th) -> dict:
    from app.core.classify import classify
    cents = lambda dollars: None if dollars is None else round(dollars * 100)        # noqa: E731
    dsls, received = item.get("dsls"), item.get("received", 365)
    rw = item.get("return_days")
    status = classify(item["qty"], None if received is None else as_of - timedelta(days=received),
                      None if dsls is None else as_of - timedelta(days=dsls), item.get("sold_90d", 0), item["category"],
                      bool(item.get("seasonal")), as_of, th)
    return {"sku": f"EVAL-{case_id}", "category": item["category"], "qty": item["qty"], "unit_cost_cents": cents(item["cost"]),
            "sell_price_cents": cents(item["price"]), "map_cents": cents(item.get("map")), "dsls": dsls, "units_sold_90d": item.get("sold_90d", 0),
            "is_seasonal": 1 if item.get("seasonal") else 0, "return_window_end": None if rw is None else (as_of + timedelta(days=rw)).isoformat(),
            "capital_cents": max(item["qty"], 0) * cents(item["cost"]), "status": status}


def check_rules(row: dict, expect: dict, as_of: date, th) -> list[str]:
    from app.core import actions
    rw = date.fromisoformat(row["return_window_end"]) if row["return_window_end"] else None
    opts = actions.allowed_options(qty=row["qty"], unit_cost=row["unit_cost_cents"], sell_price=row["sell_price_cents"], dsls=row["dsls"],
                                   units_sold_90d=row["units_sold_90d"], category=row["category"], seasonal=bool(row["is_seasonal"]),
                                   return_window_end=rw, as_of=as_of, th=th, map_price=row["map_cents"])
    got = {"status": row["status"], "allowed": set(opts.options()), "max_discount": max(opts.discounts_pct) if opts.discounts_pct else None,
           "below_cost": opts.below_cost, "map_blocked": opts.map_blocked}
    want = {"status": expect["status"], "allowed": set(expect["allowed"]), "max_discount": expect.get("max_discount"),
            "below_cost": expect.get("below_cost", False), "map_blocked": expect.get("map_blocked", False)}
    return [f"rules: {k} is {got[k]!r}, expected {want[k]!r}" for k in want if got[k] != want[k]], opts


def check_recommender(llm, row: dict, opts, as_of: date, th):
    from app.agents import recommender
    from app.facts import item_facts
    facts = item_facts(row, th, as_of)
    started = time.perf_counter()
    res = recommender.recommend(llm, row["sku"], facts, help_requested=True)
    ms = round(1000 * (time.perf_counter() - started))
    rec = res.output
    problems = []
    if rec.option not in opts.options():
        problems.append(f"recommender chose {rec.option!r}, not allowed ({opts.options()})")
    if rec.option == "discount" and rec.discount_pct not in opts.discounts_pct:
        problems.append(f"recommender chose {rec.discount_pct}%, allowed steps {list(opts.discounts_pct)}")
    if rec.status != "proposed":
        problems.append("recommendation is not 'proposed'")
    return problems, res, ms


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--backend", choices=("mock", "meta", "openai_compat"))
    p.add_argument("--json", type=Path, help="also write the results here")
    args = p.parse_args()
    from app.api.main import load_env
    load_env()
    if args.backend:
        os.environ["LLM_BACKEND"] = args.backend
    from app.core.thresholds import load_thresholds
    from app.llm.client import LLMClient
    th, llm = load_thresholds(), LLMClient()
    spec = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    as_of = date.fromisoformat(spec["as_of"])
    results = []
    for case in spec["cases"]:
        row = build_row(case["id"], case["item"], as_of, th)
        problems, opts = check_rules(row, case["expect"], as_of, th)
        rec_problems, res, ms = check_recommender(llm, row, opts, as_of, th)
        problems += rec_problems
        rec = res.output
        results.append({"id": case["id"], "title": case["title"], "pass": not problems, "problems": problems, "option": rec.option,
                        "discount_pct": rec.discount_pct, "outcome": res.outcome, "ms": ms, "notes": res.notes})
    n, passed = len(results), sum(r["pass"] for r in results)
    asked = [r for r in results if r["outcome"] != "mock"]
    fallback = sum(r["outcome"] == "fallback" for r in asked)
    print(f"Backend: {llm.backend}. {passed} of {n} cases pass ({100 * passed / n:.0f}%). "
          + (f"Recommender fallback rate: {fallback} of {len(asked)} ({100 * fallback / len(asked):.0f}%)." if asked
             else "Mock: no model was asked, so the recommender's proposals are the rules' own."))
    print("| Case | Pass | Proposal | Outcome | ms |")
    print("|---|---|---|---|---|")
    for r in results:
        what = f"discount {r['discount_pct']}%" if r["option"] == "discount" else r["option"]
        print(f"| {r['id']} | {'yes' if r['pass'] else 'NO'} | {what} | {r['outcome']} | {r['ms']} |")
    for r in results:
        for prob in r["problems"]:
            print(f"FAIL {r['id']}: {prob}")
        if r["outcome"] in ("fallback", "retried_ok"):
            print(f"note {r['id']} ({r['outcome']}): " + "; ".join(str(x)[:160] for x in r["notes"]))
    if args.json:
        args.json.write_text(json.dumps({"backend": llm.backend, "as_of": spec["as_of"], "passed": passed, "cases": n,
                                         "fallback": fallback, "asked": len(asked), "results": results}, indent=2), encoding="utf-8")
    return 0 if passed == n else 1


if __name__ == "__main__":
    sys.exit(main())
