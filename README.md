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

From an empty folder, on the offline mock model (no key, no network needed for the app itself):

```
git clone https://github.com/vmndilip-star/Loreg.git
cd Loreg
python -m venv .venv
.venv\Scripts\activate                    # macOS / Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env                    # macOS / Linux: cp .env.example .env
python -m uvicorn app.main:app
```

Then open http://127.0.0.1:8000/. In `.env`, remove the `#` in front of `LOREG_DEMO=1` to get a "Use the demo stock list" button on the Upload screen (or choose the file `data/demo/upload_demo_200.csv`). `.env` is local and never committed; `.env.example` fixes the stock date at 2026-10-26 so the demo's numbers do not change with the calendar, and uses `LLM_BACKEND=mock`.

- **Real Meta model:** put `META_API_KEY` in `.env` and set `LLM_BACKEND=meta`. `python scripts/journey_meta.py` runs the whole demo journey once on it and saves a redacted transcript under `data/generated/`.
- **More synthetic data (optional):** `python data/generate_catalog.py` writes the 3,000-product database and the demo files under `data/generated/`.
- **Tests:** `python -m pytest -q`. They run offline on the mock model. `tests/test_journey.py` is the demo journey over the HTTP API; `tests/test_web_browser.py` repeats it in Chrome through Playwright and skips itself, with a message, if Playwright or Chrome is missing. `LOREG_DEMO=1 python -m pytest -q -k "browser or journey"` runs the journey tests with the demo switch already on.

The stock list may include an optional `map_price` column (minimum advertised price, in dollars; blank means none). See "Honest limits" for what the rule does and does not do.

## Honest limits

- **One news source.** Loreg Watch reads only `news.samsung.com` (see `config/news_sources.yaml`). Other brands' feeds are not added until their `robots.txt` and terms have been read.
- **robots.txt times out.** The Samsung `robots.txt` often does not answer within the 10-second limit. A successful answer is cached for 24 hours; if it cannot be read and nothing is cached, the scan stops and does not fetch the feed. The Watch page then shows stored copies with their true dates, never as fresh.
- **The demo alert's article is synthetic.** None of the 50 real items read on 2026-10-28 announced a newer generation of a line in our catalog, so in demo mode the alert uses an article labelled "Demo article (synthetic)". Real items captured earlier are listed as "Stored copy". A real article only becomes an alert's source outside demo mode.
- **The 30-day stale limit is our assumption.** News older than `watch.stale_after_days` (30, in `config/thresholds.yaml`) is flagged as not fresh. No source or rule gave that number.
- **The MAP rule is a conservative simplification.** The `map_price` column and the rule (a discount is offered only while the price stays at or above both cost and MAP) are our own design. Real minimum-advertised-price policies are more nuanced (per brand, per channel, bundles, promotions); Loreg never offers a discount that could break the simple rule, and says why when one is blocked.
- **The sales history is assumed long enough.** Loreg assumes the export's sales history covers at least the dead threshold (see `config/thresholds.yaml`) and does not verify it. An item with no recorded sale and a received date older than that threshold is called dead; if the export only covers a shorter period, that is wrong. An item that was never sold and has no received date is shown as "Not enough data" instead of being guessed (the demo has one).
- **`openai_compat` has not been tried against a real endpoint.** The backend for any OpenAI-compatible host exists and is tested only against fakes.
- **The open-weights run was skipped.** No open-weights partner key was available, so nothing was run on an open-weights model. Every real-model run so far used the Meta Model API.
- **Everything else is synthetic.** All stock data, prices and sales are generated; they show that the app works, not that any price is true.

## Live news (Loreg Watch)

News is read only from the hosts listed in `config/news_sources.yaml` (today: `news.samsung.com`), over https, with `robots.txt`, conditional GET, a size cap and hand-checked redirects. Only a short verbatim excerpt (280 characters or fewer), the title, address, dates and a content hash are kept; the article body is cleared once it has been read. An alert shows a source only if the article has a readable publication date, and it cites the address, the published date, the fetched date and the last check. If the feed cannot be reached the alert says "Stored copy" with its true dates. News is aged against today's date. The demo's stored copies in the repo hold only titles, addresses, dates and hashes, no article text; `python scripts/capture_demo_news.py` writes the excerpts to a git-ignored local file that stays on your machine.

In demo mode (`LOREG_DEMO=1`) every launch alert carries a labelled source: a stored copy of a real article if `data/demo/news_copy.json` exists, otherwise a synthetic article labelled "Demo article (synthetic)". `python scripts/capture_demo_news.py` reads the feed once (when `robots.txt` answers) and writes `data/demo/news_items.json`, the real items listed as "Stored copy" on the Watch page; with `--pick N` it also writes the alert's stored copy. In demo mode a news scan adds real items to the list but never replaces the demo alert.

## Status

The backend (deterministic core, upload and data checks, storage, Loreg Watch, agents, streamed chat, daily brief) and the web app (Home, Stock with browse and search, Watch, Ask, Upload) work end to end on synthetic data. The QuickBooks import and the open-weights run are not done. See "Honest limits".
