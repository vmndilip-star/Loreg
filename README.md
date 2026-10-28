# Loreg

**A smart log and register for small electronics and appliance shops.**

## Core idea

A small shop owner often has money tied up in stock that has stopped selling, and every new product launch makes that stock harder to move. Loreg watches the shop's inventory, sales and market news, and tells the owner which shelf stock is at risk **before the loss grows**, so they can decide what to do with it.

("Stock" always means shelf inventory, never securities. Loreg gives no investment advice.)

## What Loreg does

- **Finds dead and slow stock** from inventory and sales data, and shows how much money is tied up.
- **Loreg Watch:** monitors manufacturer and market news for launches that put current stock at risk.
- **Loreg Daily:** a short morning summary.
- **Ask Loreg:** a chat for questions about the shop's stock.
- **One recommended action per at-risk item** (discount, bundle, return or hold). The owner accepts or declines.

## Principles

- **Alert first, then offer help.** A discount is never the first thing shown.
- **The owner makes the final call.** Recommendations are options with reasons.
- **Everything is cited.** Each alert or recommendation names the SKUs, metrics and dated sources it used, or says "not enough data".
- **Numbers come from code, not the model.** Dead-stock math is deterministic Python; the language model only explains and recommends from numbers the code computed.
- **Web content is untrusted data.** Instructions found in fetched articles are never followed.

## Scope

TVs, washing machines, washer-dryers, refrigerators, phones and laptops, for US shops.

## Hackathon

Built solo for the Meta Global AI Developer Hackathon (indie developer track, "Build for Your World").

## Prior work vs built during the hackathon

Hackathon work starts at the git tag **`hackathon-start`**. Everything in the commit with that tag is prior work, made before the event. Everything after it was built during the hackathon.

**Prior work** (all under [`prior/`](prior/), untouched since the tag; `prior/MANIFEST.sha256` lists a SHA-256 for every file):
- [`prior/dataset_v2/`](prior/dataset_v2/): the earlier synthetic dataset generator (`generate_dataset_v2.py`), its validator (`validate_dataset.py`), the classification and data-quality rules (`loreg_rules.py`), the price anchor table (`market_price_reference.csv`) and a 200-row demo upload.
- [`prior/web-prototype/`](prior/web-prototype/): a static frontend prototype (Bootstrap, five screens). It renders from a `snapshot.json` that `snapshot.py` computes from a product database CSV. It has no backend and no file upload. It was made before this repo existed.
- [`prior/docs/`](prior/docs/): the base context documents (`CONTEXT.md`, `LOREG_BASE_CONTEXT.md`).

**Built during the hackathon** (everything after `hackathon-start` is new work):
- The deterministic core, the API and storage, the model client, the agents, the 3,000-product catalog table and its generator, and the tests. Prior files are reference only; anything needed from them is reimplemented, not imported.
- [`web/`](web/) is the real frontend. Its markup, styles and layout are **derived from** `prior/web-prototype/`. All of its wiring is new work: API calls, real data, file upload, the alert-first flow, the streamed chat, and state that comes from the backend. The prototype's "purchase bill" screen is not built.

## Run it

```
pip install -e ".[dev]"
python data/generate_catalog.py            # synthetic data (optional; the demo file is bundled in data/demo/)
set LOREG_DEMO=1                           # optional: adds a "Use the demo stock list" button (PowerShell: $env:LOREG_DEMO = "1")
python -m uvicorn app.main:app             # then open http://127.0.0.1:8000/
```

`LLM_BACKEND=mock` (the default) needs no key. For the real Meta model, put `META_API_KEY` in a local `.env` (never committed) and set `LLM_BACKEND=meta`.
`python scripts/journey_meta.py` runs the whole demo journey once on the Meta backend and saves a transcript under `data/generated/`.

The stock list may include an optional `map_price` column (minimum advertised price, in dollars; blank means none). Our rule, an assumption rather than a sourced one: a discount step is only offered while the price stays at or above both cost and that price.

Tests: `python -m pytest -q`. They run offline. `tests/test_journey.py` is the demo journey over the HTTP API; `tests/test_web_browser.py` repeats it in Chrome through Playwright and skips itself if Playwright or Chrome is missing.

## Status

The backend (deterministic core, upload and data checks, storage, Loreg Watch, agents, streamed chat, daily brief) and the web app (Home, Stock, Watch, Ask, Upload) work end to end on synthetic data. Search and browse on Stock, the QuickBooks import, and the open-weights run are not done. All test data is synthetic.
