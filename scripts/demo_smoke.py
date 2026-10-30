"""The 3-minute demo path, run headless against the real app over HTTP (in process), with a throwaway database. Exit code 1 on any failure.

    python scripts/demo_smoke.py                  (the backend from LLM_BACKEND, default mock; a real backend needs its key in .env)
    python scripts/demo_smoke.py --backend meta

Steps, each checked: demo upload (data checks show quarantined rows) -> dead items and capital tied up -> Loreg Daily with citations ->
a launch alert with a source address and dates -> "What should I clear before the launch?" streamed, every item explanation grounded
in its facts -> help, plan, accept: capital moves from at risk to action in progress, the total stays, the shop's cached answers are
dropped and the old brief is marked stale -> undo restores it. Nothing here changes the app or a real database.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ASK = "What should I clear before the launch?"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{f': {detail}' if detail else ''}")
    return bool(ok)


def sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        name = data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            out.append((name, data))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--backend", choices=("mock", "meta", "openai_compat"), help="overrides LLM_BACKEND")
    args = p.parse_args()
    from app.api.main import load_env
    load_env()
    os.environ.setdefault("LOREG_LOG_LEVEL", "WARNING")            # the report is the output; the JSON log lines are for the server
    if args.backend:
        os.environ["LLM_BACKEND"] = args.backend
    os.environ["LOREG_DEMO"] = "1"                       # the demo shop and the demo alert's labelled source
    as_of = os.environ.get("LOREG_AS_OF") or "2026-10-26"

    from fastapi.testclient import TestClient

    from app.agents.base import ungrounded_numbers
    from app.api.main import create_app
    from app.facts import item_facts
    from app.storage import repo_ai
    from app.storage.db import connect

    backend = os.environ.get("LLM_BACKEND") or "mock"
    print(f"Loreg demo smoke test. Backend: {backend}. Stock date: {as_of}.")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(db_path=Path(tmp) / "smoke.sqlite", as_of=as_of)
        with TestClient(app) as c:
            s = c.get("/api/session").json()
            if not check("signed in to the demo shop", s.get("signed_in"), ""):
                return 1
            c.headers["X-CSRF-Token"] = s["csrf_token"]

            up = c.post("/api/demo/load").json()
            check("demo stock list uploaded", up.get("kept", 0) > 0, f"{up.get('rows')} rows, {up.get('kept')} kept, {up.get('blocked')} blocked")
            issues = c.get(up["issues_url"]).json()["issues"] if "issues_url" in up else []
            check("data-quality screen has something to show", up.get("blocked", 0) > 0 and issues, ", ".join(i["code"] for i in issues))

            summary = c.get("/api/summary").json()
            t = summary["totals"]
            check("dead items found", t["dead"]["n"] > 0, f"{t['dead']['n']} dead, {t['slow']['n']} slow")
            check("capital tied up is above zero", summary["money_at_risk_cents"] > 0, f"${summary['money_at_risk_cents'] / 100:,.2f}")

            brief = c.post("/api/daily/run").json()
            check("Loreg Daily written with citations", brief.get("headline") and brief.get("citations"),
                  f"source {brief.get('source')}: {brief.get('headline')!r}")

            groups = c.get("/api/launch/exposed").json()["groups"]
            src = groups[0]["source"] if groups else {}
            check("launch alert has a source address and dates", src.get("attached") and src.get("url") and src.get("published") and src.get("fetched"),
                  f"{src.get('label') or 'live'}: {src.get('url')} published {str(src.get('published'))[:10]}")

            r = c.post("/api/ask", json={"question": ASK})
            events = sse_events(r.text)
            names = [e for e, _ in events]
            done = events[-1][1] if events and events[-1][0] == "done" else {}
            check("Ask streams to the end with no error", r.status_code == 200 and names and names[0] == "status" and "error" not in names and done,
                  f"{len(events)} events, {done.get('total_ms')} ms, pipeline {done.get('pipeline')}")
            reply = "".join(d["text"] for e, d in events if e == "token")
            cards = [card for e, d in events if e == "cards" for card in d["cards"]]
            items = [card for card in cards if card["type"] == "item"]
            conn = connect(app.state.db_path)
            try:
                shop = conn.execute("SELECT id FROM shops ORDER BY id LIMIT 1").fetchone()["id"]
                loose = {}
                for card in items:
                    facts = item_facts(repo_ai.item_row(conn, shop, card["sku"]), app.state.th, date.fromisoformat(as_of))
                    bad = ungrounded_numbers(card["explanation"], facts)
                    if bad:
                        loose[card["sku"]] = bad
            finally:
                conn.close()
            check("Ask answer is grounded", reply and items and not loose,
                  f"{len(items)} item explanations, agents {[(a['agent'], a['source']) for a in done.get('agents', [])]}"
                  + (f"; numbers in no fact: {loose}" if loose else ""))

            alert_id = groups[0]["alert_id"]
            c.post(f"/api/alerts/{alert_id}/help")
            recs = c.post(f"/api/alerts/{alert_id}/plan").json()["recommendations"]
            check("one proposed option per item", recs and all(x["status"] == "proposed" for x in recs),
                  "; ".join(f"{x['sku']}: {x['option_label']} ({x['source']})" for x in recs))
            before = c.get("/api/summary").json()["exposure"]
            after = c.post(f"/api/actions/{recs[0]['id']}/accept").json()["exposure"]
            moved = before["at_risk_cents"] - after["at_risk_cents"]
            check("accept moves capital from at risk to action in progress",
                  moved == recs[0]["tied_cents"] and after["action_in_progress_cents"] - before["action_in_progress_cents"] == moved,
                  f"${moved / 100:,.2f} moved")
            check("at risk + action in progress is unchanged", after["total_cents"] == before["total_cents"])
            check("the screens read the new figures", c.get("/api/summary").json()["exposure"] == after)
            check("cached answers dropped and the old brief marked stale",
                  app.state.cache.count_shop(shop) == 0 and c.get("/api/daily").json()["stale"] is True)
            restored = c.post(f"/api/actions/{recs[0]['id']}/undo").json()["exposure"]
            check("undo restores the buckets", restored == before)

    failed = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(failed)} of {len(results)} checks passed in {time.perf_counter() - started:.1f} s.")
    if failed:
        print("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
