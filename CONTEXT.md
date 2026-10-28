# Loreg: what this repo is

Base context for anyone (human or AI assistant) working in this repo. It describes what we are doing and the rules we follow. It is not an implementation guide.

## What this repo is
Loreg is a hackathon project: an AI assistant ("a smart log and register") for small electronics and appliance shops. It is being built solo for the **Meta Global AI Developer Hackathon** (indie developer track, theme "Build for Your World"), hosted by Cerebral Valley with Meta. Building runs Oct 26 to Nov 4, 2026; submission is due Nov 4, 9:00 PM ET.

## The problem and the user
A small shop owner has money tied up in stock that has stopped selling, and product launches make that stock harder to move. The user is that owner. The job is to decide what to do with stalled stock before the loss grows.

## What Loreg does
- Finds dead and slow shelf stock from the owner's inventory and sales data, and shows how much money is tied up.
- Watches manufacturer and market news for launches that put that stock at risk (Loreg Watch).
- Gives a short daily summary (Loreg Daily) and answers questions in chat (Ask Loreg).
- Proposes one action per at-risk item (discount, bundle, return or hold). The owner accepts or declines.

"Stock" always means shelf inventory, never securities. Loreg gives no investment advice.

## Product rules
- Alert first, then ask if the owner wants help, and only then plan a discount. A discount is never the first thing shown.
- One recommended action per item, presented as an option with reasons. The owner makes the final call.
- Every recommendation or alert cites the SKUs, metrics and dated sources it used. If it cannot cite, it says "not enough data".
- Scope: TVs, washing machines, washer-dryers, refrigerators, phones and laptops, for US shops. Tablets and accessories appear only in the larger test dataset.

## Design principles
- **Numbers come from code, not the model.** All dead-stock math (days since last sale, days of stock, capital at risk, margin impact) is deterministic Python. The language model only explains and recommends from numbers the code computed.
- **One door to the model.** A single module talks to any model API, with interchangeable backends (Meta Model API, an OpenAI-compatible open-weights host, and a mock for tests and offline demos).
- **Structured, checkable output.** Agents return validated JSON, and code checks that every citation is real.
- **Web content is untrusted.** Fetched articles are treated as data. Instructions found inside them are never followed.
- **The AI is optional for correctness.** If the model is unavailable, the core numbers are still right.

## Planned parts (names only)
Deterministic core, CSV upload with data-quality checks, local database, market-news ingestion, inventory and market agents, daily brief, chat with streaming, web frontend, scheduler. Optional extras after the main path works: voice question and shelf-photo counting. Not planned for the hackathon: POS integration, supplier emails, competitor prices, push notifications, multi-shop.

## Data
Test data is **synthetic**. Brand and model names are real product lines; prices, costs, quantities and sales are generated. Market prices come from a table of anchors (cheapest regular and promo prices per category and size). Only some anchors have a source; the rest are estimates and are labeled as such. The data proves the app works, not that any price is true.

## Hackathon constraints that shape the work
- Repo must be public. Only work built during the event may be demoed.
- Work that predates the event (the planning and early dataset generators) must be disclosed in the README as prior work, with a marker for where hackathon work starts.
- Judging weights: Impact 30%, Use of Meta AI Tooling 25%, Demo 25%, Technical Depth 20%. Finalists are judged on a 3-minute video.
- Meta models must be used for real, not only a mock. Bonus prizes we are aiming at: open-weights build and agentic use-case.

## Facts we do not guess
Meta Model API request formats, model IDs, pricing and rate limits come from Meta's current docs (dev.meta.ai/docs). Hackathon rules come from the participant guide and the Official Rules. Anything unconfirmed is marked as an assumption. We never claim a domain, handle or trademark for "Loreg" is available.

## Status
Updated 2026-10-27. The backend is built and tested offline: deterministic core, CSV upload with data-quality checks, SQLite storage, Loreg Watch (Samsung newsroom feed), the agents, the three pipelines, the streamed chat and the daily brief. The Meta Model API has been called for real. The web frontend (`web/`) is in progress; see `docs/WEB_PLAN.md`. This file is a copy of `prior/docs/CONTEXT.md`, which is left unchanged.

Still open: read the Official Rules and review the submission form (neither has been done), replace estimated price anchors with a live source, verify the product names in `data/names_to_verify.csv`, and the open-weights run (no partner key yet).

## Glossary
- **Dead stock:** no sale for longer than a per-category threshold.
- **Slow stock:** selling too slowly, or too much of it on hand for its sales rate.
- **Capital at risk:** unit cost times quantity for dead or slow items.
- **Launch risk:** a known launch date arrives before current stock would sell through.
- **Loreg Daily / Watch / Ask:** the morning brief, the market alerts, the chat.
