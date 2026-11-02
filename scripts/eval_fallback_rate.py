"""Fallback rate and latency per agent, on the demo stock list, through the chosen backend. In process, throwaway database.

    python scripts/eval_fallback_rate.py                       (backend from LLM_BACKEND, default mock)
    python scripts/eval_fallback_rate.py --backend meta --items 5 --runs 1

Workload per run: the demo upload; Loreg Daily; the three suggested questions; "Why is <SKU> stuck?" for the top --items at-risk items;
then help and a plan for up to 10 items of the first alert. Every agent call is read back from the agent_calls table the app writes:
outcome (model_ok, retried_ok, fallback, mock) and latency. Cached answers are counted but left out of the latency figures.
The fallback rate counts only calls a real model was asked; on mock no model is asked, so it is "n/a". Nothing here changes the app.
"""

import argparse
import json
import math
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QUESTIONS = ["What should I clear before the launch?", "How much money is tied up?", "Give me today's summary."]


def pct(values: list[int], p: float):
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def one_run(items: int) -> list[dict]:
    from fastapi.testclient import TestClient

    from app.api.main import create_app
    from app.storage.db import connect
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(db_path=Path(tmp) / "eval.sqlite", as_of=os.environ.get("LOREG_AS_OF") or "2026-10-26")
        with TestClient(app) as c:
            from _session import sign_in_demo
            sign_in_demo(c, app)
            assert c.post("/api/demo/load").status_code == 200
            c.post("/api/daily/run")
            for q in QUESTIONS:
                c.post("/api/ask", json={"question": q})
            for it in c.get("/api/inventory/top", params={"limit": items}).json()["items"]:
                c.post("/api/ask", json={"question": f"Why is {it['sku']} stuck?"})
            groups = c.get("/api/launch/exposed").json()["groups"]
            if groups:
                c.post(f"/api/alerts/{groups[0]['alert_id']}/help")
                c.post(f"/api/alerts/{groups[0]['alert_id']}/plan", params={"limit": 10})
        conn = connect(app.state.db_path)
        try:
            return [dict(r) for r in conn.execute("SELECT agent, outcome, ms, cached, backend, pipeline FROM agent_calls ORDER BY id")]
        finally:
            conn.close()


def summarise(calls: list[dict]) -> dict:
    by = defaultdict(list)
    for c in calls:
        by[c["agent"]].append(c)
    by["ALL"] = list(calls)
    out = {}
    for agent, rows in by.items():
        counts = {o: sum(1 for r in rows if r["outcome"] == o) for o in ("model_ok", "retried_ok", "fallback", "mock")}
        asked = len(rows) - counts["mock"]
        live = [r["ms"] for r in rows if not r["cached"]]
        out[agent] = {"calls": len(rows), **counts, "fallback_rate": round(counts["fallback"] / asked, 4) if asked else None,
                      "p50_ms": pct(live, 50), "p95_ms": pct(live, 95), "cached": sum(1 for r in rows if r["cached"])}
    return out


def table(summary: dict) -> str:
    head = "| Agent | Calls | model_ok | retried_ok | fallback | mock | Fallback rate | p50 ms | p95 ms | Cached |"
    lines = [head, "|" + "---|" * 10]
    for agent in sorted(summary, key=lambda a: (a == "ALL", a)):
        s = summary[agent]
        rate = "n/a" if s["fallback_rate"] is None else f"{100 * s['fallback_rate']:.1f}%"
        lines.append(f"| {agent} | {s['calls']} | {s['model_ok']} | {s['retried_ok']} | {s['fallback']} | {s['mock']} | {rate} | "
                     f"{s['p50_ms'] if s['p50_ms'] is not None else '-'} | {s['p95_ms'] if s['p95_ms'] is not None else '-'} | {s['cached']} |")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--backend", choices=("mock", "meta", "openai_compat"))
    p.add_argument("--items", type=int, default=5, help="SKU questions per run (default 5)")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--json", type=Path, help="also write the summary here")
    args = p.parse_args()
    from app.api.main import load_env
    load_env()
    os.environ.setdefault("LOREG_LOG_LEVEL", "WARNING")            # the report is the output; the JSON log lines are for the server
    if args.backend:
        os.environ["LLM_BACKEND"] = args.backend
    os.environ["LOREG_DEMO"] = "1"
    backend = os.environ.get("LLM_BACKEND") or "mock"
    started = time.perf_counter()
    calls = []
    for _ in range(args.runs):
        calls += one_run(args.items)
    summary = summarise(calls)
    print(f"Backend: {backend}. {args.runs} run(s), {len(calls)} agent calls, {time.perf_counter() - started:.1f} s.")
    if backend == "mock":
        print("Mock backend: no model was asked, so there is no fallback rate; the latencies are the app's own.")
    print(table(summary))
    if args.json:
        args.json.write_text(json.dumps({"backend": backend, "runs": args.runs, "summary": summary}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
