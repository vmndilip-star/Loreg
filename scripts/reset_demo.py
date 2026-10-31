"""Reset to a known first-run state: delete the app's database and load the demo shop, through the app's own routes.

    python scripts/reset_demo.py --yes

Deletes the database at LOREG_DB (default data/loreg.sqlite) with its -wal and -shm files, creates a fresh one, signs in to the demo
shop (LOREG_DEMO=1 for this run only) and loads the bundled demo stock list, so the next `python -m uvicorn app.main:app` with
LOREG_DEMO=1 opens on the same numbers every time. The stock date is LOREG_AS_OF, else 2026-10-26. Stop the server first: Windows will not delete a database that is open.
Every account and decision in that database is lost; nothing else is touched.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEMO_AS_OF = "2026-10-26"                                   # the stock date .env.example sets


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--yes", action="store_true", help="really delete the database")
    args = p.parse_args()
    from app.api.main import load_env
    load_env()
    os.environ.setdefault("LOREG_LOG_LEVEL", "WARNING")            # the report is the output; the JSON log lines are for the server
    from app.storage.db import DEFAULT_DB_PATH
    db = Path(os.environ.get("LOREG_DB") or DEFAULT_DB_PATH)
    if not args.yes:
        print(f"This deletes {db} and every account in it. Run again with --yes to go ahead.")
        return 2
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            print(f"Could not delete {path}: is the server still running? Stop it and try again.")
            return 1
    os.environ["LOREG_DEMO"] = "1"
    from fastapi.testclient import TestClient

    from app.api.main import create_app
    as_of = os.environ.get("LOREG_AS_OF") or DEMO_AS_OF                 # fixed, so the numbers do not move with the calendar
    with TestClient(create_app(db_path=db, as_of=as_of)) as c:
        from _session import sign_in
        sign_in(c)
        up = c.post("/api/demo/load")
        if up.status_code != 200:
            print(f"Loading the demo stock list failed: {up.status_code} {up.text[:200]}")
            return 1
        u, m = up.json(), c.get("/api/summary").json()
    print(f"Fresh database at {db}. Demo shop loaded: {u['rows']} rows, {u['kept']} kept, {u['blocked']} quarantined; "
          f"money at risk ${m['money_at_risk_cents'] / 100:,.2f}, stock date {m['as_of']}.")
    print("Start the app with LOREG_DEMO=1 (in .env) to open the demo shop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
