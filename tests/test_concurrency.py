"""SQLite under the agent thread pool: WAL, a busy timeout, foreign keys, one connection per thread and short write transactions.
Eight threads plan, accept, undo, record agent calls and read the summary at once on one database file; none may see "database is locked"
and the money figures must still add up. Offline (the deterministic stand-in model)."""

import random
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from app.api.main import create_app
from app.core.thresholds import load_thresholds
from app.pipelines import Ctx, ResultCache, plan_alert
from app.storage import repo, repo_ai
from app.storage.db import connect
from auth import AuthedClient
from conftest import OfflineModel


def test_every_connection_is_wal_with_a_busy_timeout_and_foreign_keys(tmp_path):
    conn = connect(tmp_path / "c.sqlite")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def run_plan(db, shop_id, alert_id, th, executor):
    conn = connect(db)
    try:
        ctx = Ctx(conn=conn, shop_id=shop_id, th=th, as_of=date(2026, 10, 26), today=date(2026, 10, 26), llm=OfflineModel(),
                  cache=ResultCache(), executor=executor)
        gen = plan_alert(ctx, alert_id, 10)
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
    finally:
        conn.close()


def test_parallel_plans_decisions_and_reads_never_lock_the_database(catalog_dir, tmp_path):
    db = tmp_path / "c.sqlite"
    c = AuthedClient(create_app(db_path=db, as_of="2026-10-26", llm=OfflineModel()))
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert c.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
    alert_id = c.get("/api/launch/exposed").json()["groups"][0]["alert_id"]
    c.post(f"/api/alerts/{alert_id}/help")
    rec_ids = [r["id"] for r in c.post(f"/api/alerts/{alert_id}/plan", params={"limit": 10}).json()["recommendations"]]
    with sqlite3.connect(db) as con:
        shop_id = con.execute("SELECT id FROM shops ORDER BY id LIMIT 1").fetchone()[0]
    total = c.get("/api/summary").json()["exposure"]["total_cents"]
    th = load_thresholds()
    errors, lock = [], threading.Lock()
    agents = ThreadPoolExecutor(max_workers=8)

    def worker(seed):
        rnd = random.Random(seed)
        for _ in range(25):
            step = rnd.choice(("accept", "undo", "plan", "record", "read"))
            conn = connect(db)
            try:
                if step == "accept":
                    repo.decide(conn, shop_id, rnd.choice(rec_ids), True)
                elif step == "undo":
                    repo.undo(conn, shop_id, rnd.choice(rec_ids))
                elif step == "plan":
                    run_plan(db, shop_id, alert_id, th, agents)
                elif step == "record":
                    repo_ai.record_agent_calls(conn, shop_id, f"run-{seed}", "plan", "mock", [("recommender", "mock", 3, False)])
                else:
                    e = repo.money_at_risk(conn, shop_id)["exposure"]
                    if e["total_cents"] != total:
                        raise AssertionError(f"total moved: {e}")
            except repo.Conflict:
                pass                                                   # already accepted, nothing to undo: expected, not a lock
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(f"{step}: {type(e).__name__}: {e}")
            finally:
                conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    agents.shutdown(wait=True)
    assert not errors, errors[:5]
    assert not any("locked" in e for e in errors)
    final = c.get("/api/summary").json()["exposure"]
    assert final["total_cents"] == total and final["at_risk_cents"] + final["action_in_progress_cents"] == total
