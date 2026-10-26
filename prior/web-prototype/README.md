# Loreg web (glass bento, Bootstrap 5.3.3)

Responsive frontend prototype. Layout comes from Bootstrap's grid; the glass look is `styles.css`.
**Every number on screen comes from `snapshot.json`, which is computed from the product database by code.**

    python snapshot.py --master path/to/skus_master.csv --as-of 2026-10-26 --out snapshot.json
    python -m http.server        # then open http://localhost:8000  (fetch needs http, not file://)

- `snapshot.py`   reads the database CSV (Loreg upload format) and writes snapshot.json: totals, dead/slow capital, by-category bars,
                  biggest items, launch-exposed items with one option each, upload data-check counts, sample bill. Uses `loreg_rules.py`.
                  Cross-checks its dead/slow totals against summary.csv when that file sits next to the master.
- `loreg_rules.py` classification + data-quality rules (prior work copy; the app's real version belongs in app/core).
- `index.html`    five views (Home, Stock, Watch, Ask, Upload), routed by `#home #stock #watch #ask #upload`
- `app.js`        renders from the snapshot; shared "money at risk" state; Accept / Decline / Undo; alert-first rule
- `build.py`      `python build.py bootstrap.min.css out.html` inlines Bootstrap, CSS, JS and the snapshot into one file

Change the database -> rerun snapshot.py -> every figure updates. Nothing is typed into the HTML.

Known limits
- Launch exposure uses the `launch_family` flag in ground_truth.csv (Samsung 2024 TV lines in the sample data). The real feature needs a dated source.
- Option rules (discount ladder, return, below-cost discount, bundle, hold) are the prototype's version of app/core/actions.
- Upload and bill reading are display-only. The chat answers one question.
- Bracketed text is a placeholder until a real, dated source is attached.
