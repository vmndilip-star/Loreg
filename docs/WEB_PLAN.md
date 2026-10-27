# Plan: `web/` wired to the real API

Status: proposed, not started. Written 2026-10-27. Hackathon work (built during the event).
Design is derived from the prior prototype in `prior/web-prototype/` (glass bento look, five views). `prior/` is read-only and is not edited.

## Goal
One web app, served by the same FastAPI process, where every number on screen comes from the API and the API's numbers come from code. The whole demo path works in the browser:

upload a stock file -> dead and slow stock appears -> the daily brief reads it -> the launch alert comes first -> the owner asks for help -> chat answers -> the owner accepts an action -> capital at risk drops.

## Rules for the frontend
1. **No number is typed in HTML or computed in JS.** JS formats integer cents for display only (one `money()` function). After any accept, decline or undo, the page re-fetches `/api/summary` and shows what the server says. JS never adds or subtracts money.
2. **Alert first.** Home shows the alert and one question ("Want me to plan what to do with them?"). Nothing with a discount is rendered until help is recorded (`POST /api/alerts/{id}/help`).
3. **Owner decides.** Every option card has Accept and Decline, and Undo after a decision.
4. **API text is data.** Everything from the API (item names, article titles, model replies) is inserted with `textContent`, never `innerHTML`. Article titles come from untrusted sources and carry an "untrusted source" label. A meta Content-Security-Policy forbids inline script and remote origins.
5. **No model in the browser.** The browser only talks to `/api/*`. The key stays on the server.
6. **Honest empty and error states.** No data yet, model down (fallback text is shown with a "written by rules, not the model" note when `source` says so), network down, 409/404/422 from the API (show the API's own `detail`).
7. **Accessible and mobile first.** Keyboard reachable, visible focus, 48 px targets, `prefers-reduced-motion`, 16 px side gutter, no horizontal scroll at 360 px.

## Serving
- `web/` is plain static files (HTML, CSS, ES modules). **No build step, no npm in the runtime.**
- `app/api/main.py` mounts `web/` at `/` after the `/api` routes (Starlette `StaticFiles(html=True)`). One origin, so no CORS.
- Bootstrap 5.3.3 CSS is vendored in `web/vendor/` (MIT licence file kept next to it) so the demo works offline. The prototype used a CDN; that is dropped.
- Run: `uvicorn app.main:app` then open `http://127.0.0.1:8000/`.

## Files
```
web/
  index.html            shell: header, five view containers, dock, CSP meta
  styles.css            glass tokens (light + dark), layout; adapted from the prototype
  vendor/bootstrap.min.css (+ LICENSE)
  js/api.js             fetch wrapper: JSON, errors -> {status, detail}; SSE reader for /api/ask
  js/format.js          money(cents), date(iso); nothing else
  js/dom.js             el(tag, attrs, ...children) using textContent only
  js/state.js           which alert, help requested, current upload id; no money state
  js/views/home.js      alert, one question, daily brief, key numbers
  js/views/stock.js     top items, by-status counts
  js/views/watch.js     launch-exposed items with sources, option cards, Accept/Decline/Undo
  js/views/ask.js       chat over SSE
  js/views/upload.js    file drop, data-quality report
  js/app.js             hash router (#home #stock #watch #ask #upload), refresh()
tests/test_web_journey.py   demo test (below)
tests/test_web_static.py    static checks (no innerHTML, CSP present, no remote URLs)
```

## Screens and the endpoints behind them
| view | shows | endpoints |
|---|---|---|
| Home | "Money at risk, no plan yet" and at-risk total; dead / slow counts; the alert (launch line, items, source or "no dated source attached"); one question with Yes / Not now; the daily brief | `GET /api/summary`, `GET /api/launch/exposed`, `GET /api/alerts/{id}`, `POST /api/alerts/{id}/help`, `GET /api/daily`, `POST /api/daily/run` |
| Stock | top items by money tied up, status, days since last sale, needs-a-look list | `GET /api/inventory/top`, `GET /api/needs-look` |
| Watch | launch-exposed items; dated source if one is attached; option card per item after help; Accept / Decline / Undo | `GET /api/launch/exposed`, `GET /api/watch/items`, `POST /api/watch/scan`, `POST /api/alerts/{id}/plan`, `POST /api/actions/{id}/accept`, `/decline`, `/undo` |
| Ask | chat; steps shown as they stream; cards (alert, items, options) built from the `cards` event | `POST /api/ask` (text/event-stream) |
| Upload | file drop; rows OK / to look at / blocked; problem list in plain language | `POST /api/upload`, `GET /api/uploads/{id}/issues` |

Chat uses `fetch` plus a stream reader (not `EventSource`, which cannot POST). Events handled: `status`, `tool`, `token`, `cards`, `done`, `error`. Tokens append to the reply; cards render only after `cards` arrives; Recommender cards appear only when `help_requested` is true.

First run (no upload yet): Home shows "Upload your stock list to begin" and links to Upload. A demo-data button is an open question below.

## The demo test
Real server, real SQLite, real HTTP, mock model backend (offline, deterministic), fixed `LOREG_AS_OF=2026-10-26`. A browser drives the page.

1. Start `uvicorn` on a free port with a temp database and the demo upload from `data/generate_catalog.py`.
2. **Upload:** open `#upload`, attach `upload_demo_200.csv`. Assert rows OK / to look at / blocked equal the counts recomputed independently from the CSV (`tests/csv_truth.py`, no app imports).
3. **Dead stock:** open `#stock`. Assert the dead and slow counts and the top item's capital equal the CSV truth.
4. **Daily brief:** open `#home`, press run. Assert the brief shows and its headline contains the money-at-risk figure from the truth.
5. **Alert first:** Home shows the alert and the question, and **no option card or discount text is in the DOM yet**.
6. **Chat:** open `#ask`, send "what is stuck?". Assert the first status appears before the answer, tokens stream, cards arrive, and no option card appears before help.
7. **Help, then plan:** press "Yes, plan it". Assert option cards appear, one per item, each from the allowed set.
8. **Accept:** accept the first option. Assert the money-at-risk figure on screen equals truth minus that item's tied-up capital, and equals `GET /api/summary` (`money_at_risk_cents`).
9. **Undo:** the figure returns to the starting value. Decline another and assert it does not change.
10. **Security check:** feed a poisoned article through a synthetic feed fixture (`/api/watch/scan` with a fake transport) and assert its title renders as text and no script runs.

Tooling: Playwright for Python as a **dev-only** dependency, using the Chrome or Edge already on this machine (`channel="chrome"`), so no browser download. If Playwright or a browser is missing, the browser test **skips with a clear reason** rather than passing silently. An API-level twin (`TestClient`) runs the same ten-step chain without a DOM and always runs, so the chain is guarded even where there is no browser.

## Order of work and commits (stop points)
| step | content |
|---|---|
| W0 | this plan, plus `CONTEXT.md` copied to the repo root (this commit) |
| W1 | static mount, `index.html` shell, theme tokens, router, `api.js`, `format.js`, `dom.js`, static checks |
| W2 | Upload view and its tests |
| W3 | Home and Stock views |
| W4 | Watch view: option cards, Accept / Decline / Undo, alert-first |
| W5 | Ask view over SSE |
| W6 | demo test (browser plus API twin), then mobile and accessibility pass, README section |
| **stop** | report: files, screenshots at 360 px and desktop, test results, anything that did not fit |

Not in this plan (separate tasks, in this order): browsing and search on Stock, then the QuickBooks import.

## Open decisions
1. **Demo data button.** For the 3-minute video the owner should not hunt for a file. Recommended: a small `POST /api/demo/load` that loads the generator's demo upload, enabled only when `LOREG_DEMO=1`. Without it, the demo drags the file in by hand.
2. **Browser test dependency.** Recommended: Playwright (dev only, system Chrome). The alternative is the API-level chain only, which proves the numbers but not the page.
3. **Brand name in the header.** "Loreg" only; no logo or domain claim.

## Things I will not do
No invented numbers, dates, sources or prices on screen. No placeholder text in square brackets (the prototype had some; they are replaced by real data or the plain "no dated source attached"). No third-party scripts or fonts from the web.
