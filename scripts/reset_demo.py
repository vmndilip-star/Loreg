"""Reset to a known first-run state: delete the app's database, make the demo account (BigDeal) and load the demo stock list into it,
through the app's own routes.

    python scripts/reset_demo.py --yes

Deletes the database at LOREG_DB (default data/loreg.sqlite) with its -wal and -shm files and creates a fresh one with one account: the
demo account, BigDeal (email LOREG_DEMO_EMAIL and password LOREG_DEMO_PASSWORD, else the public demo credential in the README). It
signs in to it as the sign-in page would and loads the bundled demo stock list (LOREG_DEMO=1 for this run only). The next
`python -m uvicorn app.main:app` with LOREG_DEMO=1 opens on the sign-in page; "Use demo account" fills the credential in, and the numbers
are the same every time. The stock date is LOREG_AS_OF, else 2026-10-26. Stop the server first: Windows will not delete a database that
is open. Every account and decision in that database is lost; nothing else is touched. Refused when LOREG_ENV=production.
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
    from app.demo import production
    from app.storage.db import DEFAULT_DB_PATH
    if production():
        print("LOREG_ENV=production: the demo account is never made there. Nothing was changed.")
        return 2
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

    from _session import sign_in_demo
    from app.api.main import create_app
    as_of = os.environ.get("LOREG_AS_OF") or DEMO_AS_OF                 # fixed, so the numbers do not move with the calendar
    app = create_app(db_path=db, as_of=as_of)
    with TestClient(app) as c:
        s = sign_in_demo(c, app)
        up = c.post("/api/demo/load")
        if up.status_code != 200:
            print(f"Loading the demo stock list failed: {up.status_code} {up.text[:200]}")
            return 1
        u, m = up.json(), c.get("/api/summary").json()
        c.post("/api/logout")
    print(f"Fresh database at {db}. {s['profile']['org_name']} loaded: {u['rows']} rows, {u['kept']} kept, {u['blocked']} quarantined; "
          f"money at risk ${m['money_at_risk_cents'] / 100:,.2f}, stock date {m['as_of']}.")
    print(f"Start the app with LOREG_DEMO=1 (in .env) and sign in as {s['profile']['email']} (\"Use demo account\" fills it in).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
