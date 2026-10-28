# Agent framework report

Written on 2026-10-28 from the code at commit `ad3b696` and the dependency file. Where a statement could not be confirmed from the code
or from a result stored in this repository, it is marked **UNVERIFIED**.

## 1. Dependencies

There is no lockfile and no requirements file. `pyproject.toml` lists the runtime dependencies without pinned versions (only
`pydantic>=2` and `openai>=1.40` have a lower bound). The versions below are the ones installed in the development environment
where the test suite was run (`pip show`, Python 3.13.2); another install may resolve to different versions.

| Runtime dependency (pyproject.toml) | Constraint | Installed here | Used for |
|---|---|---|---|
| fastapi | none | 0.162.0 | HTTP API (`app/api/`) |
| uvicorn | none | 0.49.0 | ASGI server (`python -m uvicorn app.main:app`, `tests/live_server.py`) |
| pydantic | >=2 | 2.13.4 | request bodies and every model output schema (`app/schemas/`) |
| openai | >=1.40 | 1.99.9 | the HTTP client for OpenAI-compatible Chat Completions, used only in `app/llm/client.py` (and the one-off `scripts/api_check.py`) |
| python-dotenv | none | 1.2.2 | reads a local `.env` |
| pyyaml | none | 6.0.2 | `config/*.yaml`, `data/catalog_lines.yaml` |
| python-multipart | none | 0.0.32 | CSV upload form |
| feedparser | none | 6.0.14 | RSS parsing in Loreg Watch |
| httpx | none | 0.28.1 | allowlisted news fetching (`app/watch/feeds.py`) |
| starlette | (via fastapi) | 1.3.1 | static files, streaming response |

Development only (`[project.optional-dependencies] dev`): pytest 9.1.1, httpx, playwright 1.64.1 (drives the system Chrome).

**Agent or orchestration framework: none. No agent framework; custom code.**

Evidence. A search over every `.py` file outside `prior/` for imports of `langchain`, `langgraph`, `llama_index`, `crewai`, `autogen`,
`semantic_kernel`, the OpenAI Agents SDK package (`agents`), `openai`, `anthropic`, `haystack`, `dspy`, `pydantic_ai`, `smolagents`,
`instructor`, `guidance` and `marvin` returns exactly two lines:

```
app/llm/client.py:25:     from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
scripts/api_check.py:33:  from openai import APIStatusError, OpenAI, RateLimitError
```

`openai` is the plain HTTP client library: only `OpenAI(...).chat.completions.create(...)` and its error classes are used. None of
the framework packages appears in `pyproject.toml`. The full list of top-level imports under `app/`, `scripts/` and `data/*.py` is
the standard library, the project's own `app` package, and fastapi, starlette, pydantic, yaml, httpx, dotenv, openai and feedparser.

## 2. Agents and pipelines

Shared pieces used below:

- **`LLMClient.complete_json`** (`app/llm/client.py`): the only model call any agent makes. It sends a system and a user message with
  `response_format = {"type": "json_schema", "strict": true, "schema": strictify(Model.model_json_schema())}`, validates the reply with
  `Model.model_validate_json`, and on a validation error sends one repair message ("That did not validate: ... Return corrected JSON
  only.") before raising `LLMError`. `finish_reason == "length"` raises `LLMError`. A 429 is retried with backoff and `Retry-After`, up
  to 5 attempts; any other HTTP or connection error raises `LLMError` with the key scrubbed. With `LLM_BACKEND=mock` (the default)
  no request is made and the caller's `mock_output()` is returned.
- **`run_grounded`** (`app/agents/base.py`): used by the inventory, recommender and daily agents. The user message is `FACTS` (rendered
  from `app/facts.py`) plus `TASK`. The system prompt is the agent's `SYSTEM` plus `GROUNDING_RULES`. After the reply it strips
  brackets from citation ids and runs `check_grounding`: every citation must be a real fact id; there must be at least one citation
  unless the text starts with "Not enough data"; every number in the text must appear in some fact's id, SKU, label, value or date.
  Then the agent's own `extra_checks`. Any problem, or an `LLMError`, returns the agent's deterministic fallback with source
  `fallback`.
- **`AgentCall`** (`app/pipelines.py`): runs an agent call on the app's `ThreadPoolExecutor` (8 workers), bounds the whole call by
  `AGENT_TIMEOUT_S = 20.0` seconds, and on timeout or any exception returns the fallback. Results with source `model` or `mock` are
  cached in memory by a SHA-256 of the agent name, the facts and an extra key (`ResultCache`, 512 entries); fallbacks are never cached.

| | Router | Inventory | Market | Recommender | Daily brief |
|---|---|---|---|---|---|
| File and function | `app/agents/router.py` `route()`; called by `app/pipelines.py` `choose()` | `app/agents/inventory.py` `explain()`; called by `pipelines._inventory_call` | `app/agents/market.py` `read_article()`; called by `app/watch/scan.py` `extract_events()` | `app/agents/recommender.py` `recommend()`; called by `pipelines._recommender_call`, only inside `plan_alert` | `app/agents/daily.py` `brief()`; called by `pipelines.daily_brief` |
| Purpose | Classify a free-text question into an intent | Explain why one item's stock is tied up | Extract launch events from one fetched article | Pick one allowed action for one item | Morning summary |
| Inputs | Question (the API accepts 1 to 500 characters; at most 1,000 are sent), the shop's SKU set | `Fact` list from `app/facts.py` `item_facts` for that SKU | `Article`: source id, outlet, URL, published date, sanitized text, title | `Fact` list for that SKU; `help_requested` | `Fact` list from `app/facts.py` `summary_facts` |
| Output model (`app/schemas/outputs.py`) | `RouteDecision` (intent, agent, sku) | `InventoryExplanation` (sku, summary, citations) | `MarketExtraction` (list of `LaunchEvent`), wrapped by code in `MarketReading` | `Recommendation` (sku, option, discount_pct, reasons, citations, status fixed to "proposed"; a validator ties discount_pct to the option) | `DailyBrief` (headline, bullets, citations) |
| Calls the model | Yes, except when code decides first: instruction-like text (`detect_injection`) goes to "unclear" and future price or date questions go to "unknown_fact" with no model call | Yes | Yes | Yes | Yes |
| Backend call | `complete_json` (max_completion_tokens 4000) | `complete_json` via `run_grounded` | `complete_json` | `complete_json` via `run_grounded` | `complete_json` via `run_grounded` |
| Prompt location | `router.SYSTEM`; user message `QUESTION\n...` | `inventory.SYSTEM` + `base.GROUNDING_RULES`; task string in `explain()` | `market.SYSTEM`; user message built in `read_article()` with the text inside `<untrusted_source>` tags (`app/core/untrusted.py` `wrap_untrusted`) | `recommender.SYSTEM` + `GROUNDING_RULES`; the allowed options and percentages are written into the task string | `daily.SYSTEM` + `GROUNDING_RULES` |
| Validation layers | Strict schema and Pydantic (with one repair); code replaces the agent with `AGENT_FOR_INTENT[intent]` and blanks a SKU that is not in stock; the pipeline must be in `PIPELINES` | Schema and Pydantic; grounding; the answer must be for the requested SKU | Text sanitized before storage (`app/watch/sanitize.py`); injection flags; schema and Pydantic; `_enforce`: a quote not found in the article is dropped, an event whose quote looks like instructions is dropped, a date not written in full in the article is removed; catalog matching by code (`app/watch/matching.py`); events from a flagged article never attach to an alert | Schema; the Pydantic validator; grounding; option must be in `allowed_options(facts)` and a discount in `allowed_discounts(facts)`; then, in `plan_alert`, `_rules_say_ok` checks the answer again against the rules in `app/core/actions.py` (via `repo.item_options`) and audits a refusal | Schema and Pydantic; grounding; at most four bullets |
| Fallback | `_keyword_route` (regular expressions) on `LLMError`; `AgentCall` timeout or error also gives `_keyword_route` | `_fallback`: the facts' labels and values; "Not enough data" with no facts | `_fallback`: the first launch sentence with a full date, then one with a day and month, then one with no date | `_fallback`: the same order as `app/core/actions.default_option` (smallest profitable discount, return, below-cost discount, bundle, hold) | `_fallback`: the facts in a fixed priority order |
| Time bound | `AgentCall`, 20 s | `AgentCall`, 20 s | No `AgentCall`: only the client's 20 s per-request timeout and its 429 retries; at most 10 articles per scan, read one after another | `AgentCall`, 20 s | `AgentCall`, 20 s |
| Tests | `test_agents.py` (keyword router, code decides the agent and checks the SKU, model down); `test_pipelines.py` (allowlist, injection never reaches the router model, a steering model is overruled, unknown intent, 30 questions including adversarial ones) | `test_agents.py` (grounded answer, made-up number, unknown citation, no citation, model down, no facts, bracketed ids); `test_pipelines.py` (SKU questions, removed facts give "Not enough data", a number from a removed fact is rejected) | `test_agents.py` (injection flagged and still data, invented date removed, invented quote dropped, article wrapped, model down); `test_watch.py`; `test_news_*.py` | `test_agents.py` (no plan before help, return window, stays proposed, disallowed option, discount above the cap, ladder values); `test_plan_truth.py` (chat and Watch agree, restart, decided plan never rewritten, MAP-blocked discount overruled, second layer catches a bad answer when the agent's checks are bypassed); `test_pipelines.py` (output always in the allowed set, hostile recommender) | `test_agents.py` (fallback order, too many bullets); `test_pipelines.py` (stored and served, refused with no stock, via chat) |

The three entries below are code paths, not agents:

**Chat.** `app/api/routes_ask.py` `ask()` streams `app/pipelines.py` `answer()`. The input is `AskBody` (`question` 1 to 500 characters,
optional `sku` up to 40). A `sku` in the body that is not in stock is a 404. The output is a stream of events. The flow:

1. The first event, `status`, is sent before any model call.
2. A SKU-shaped token in the question that is not in stock ends the reply with an `error` event.
3. A `sku` in the body goes straight to the item question with no router.
4. Otherwise `choose()` decides, and the intent maps through `PIPELINE_FOR_INTENT` to one of `PIPELINES`. Anything else gets a fixed
   clarifying sentence.

The chat makes no model call of its own; it calls the router and the agents.

Tests:
- `test_pipelines.py`: event order, first status before any model call, bounded fields, invented SKUs.
- `test_web_browser.py`: the chat in Chrome.
- `tests/web/units.mjs`: the SSE reader.

**Unknown-fact path.** `app/pipelines.py` `not_enough_data()`, selected when `router.unknown_fact_kinds()` (regular expressions for
future prices, rival prices and launch or ship dates) matches. It runs before the router model, so the model is never asked.

- It says "Not enough data" and states only stored figures: the alert, its source, and the money at risk.
- A date question with an attached source is answered "From your stored news only", and says when that source is the synthetic demo
  article.

Tests: `test_pipelines.py` (the Samsung question, future price or date questions, ordinary questions not mistaken for them, a model
cannot talk the router out of it), `test_news_provenance.py` and `test_pace.py`.

**`plan_alert`.** `app/pipelines.py` `plan_alert(ctx, alert_id, limit)` is a generator. It is the only place a plan is made, for the
chat (`launch_clear`, after help) and for `POST /api/alerts/{id}/plan` (`app/api/routes_plan.py`). The steps:

1. `repo.plan` stores the rules plan for every item first, and raises `Conflict` (HTTP 409) until the owner has asked for help.
2. The recommender is asked, in parallel, only for items whose stored plan is still the rules' proposal.
3. Each answer is checked by `_rules_say_ok`. A refusal keeps the rules plan and is audited (`repo_ai.reject_agent_answer`).
4. A decided plan is never rewritten, and the stored plans are returned.

It calls the model only through the recommender. Tests: `test_plan_truth.py`, `test_pipelines.py`, `test_journey.py`.

## 3. Orchestration

- **Choosing a pipeline.** `choose()` in `app/pipelines.py` applies three rules in this order:
  1. Instruction-like text becomes `unclear`.
  2. A future price or date becomes `unknown_fact`.
  3. Otherwise the router is called through `AgentCall`.

  The router returns only an intent. Code maps it with `PIPELINE_FOR_INTENT` and `answer()` refuses anything outside `PIPELINES`.
  Agents never call each other.
- **The allowlists.**
  - `PIPELINES = ("launch_clear", "sku_question", "daily_brief")` is the full set of pipelines. Each is a hard-coded sequence of
    steps in a Python generator.
  - `AGENT_FOR_INTENT` in `router.py` fixes the agent for each intent.
  - Separately, web fetching is limited to the hosts in `config/news_sources.yaml` (`app/watch/allowlist.py` `check_url`).
- **Streaming.** Each pipeline yields events, and `routes_ask.sse()` writes each one as `event: <name>\ndata: <json>\n\n` in a
  `StreamingResponse` with `text/event-stream`, `Cache-Control: no-cache` and `X-Accel-Buffering: no`. There are six events:
  - `status`: progress text.
  - `tool`: a code step and its source (`model`, `mock` or `fallback`).
  - `token`: the reply text.
  - `cards`: built by code from stored rows.
  - `done`: per-agent timings and the money at risk.
  - `error`.

  The reply text is produced and checked first, then sent in five-word pieces (`pipelines.tokens`), so raw model tokens are never
  streamed. `LLMClient.stream_text` exists but no code under `app/` calls it. The browser reads the stream with `web/js/api.js`
  `askStream`.
- **Tool or function calling.** The model API's own tool calling is not used. No code under `app/` passes `tools` or `tool_choice`
  (search result: no match). The `tool` events above name code steps chosen by the pipeline, such as `stored_market_event`,
  `inventory_facts`, `inventory_agent`, `recommender_agent`, `shop_facts` and `daily_agent`; the model does not choose them. Every model
  call is a single structured-output request (`response_format` with a strict JSON schema).
  - `scripts/api_check.py` contains a one-off tool-call test against the Meta Model API with one fake read-only tool. Whether that
    test passes against the current API is **UNVERIFIED**: it was not run for this report and no result is stored in the repository.
  - The notes in the `app/llm/client.py` docstring about Meta's API are **UNVERIFIED** here: that reasoning tokens count toward
    `max_completion_tokens`, that `tool_choice` supports only "auto", and that `reasoning_effort` "none" is rejected. They are taken
    from the code's comments, which cite dev.meta.ai/docs. The tests check only that the client itself refuses "none".
- **Evidence of the real backend.** `docs/JOURNEY_META_FINAL.md` records three runs of `scripts/journey_meta.py` against the Meta
  Model API (`muse-spark-1.3`, reasoning effort `minimal`). Run 1 passed 23 of 24 checks: the daily brief fell back, cause not
  captured. Runs 2 and 3 passed 24 of 24 with no fallback. That shows structured output working end to end on that day, for those
  calls.

## 4. Why custom code rather than a framework

These reasons come from what the code does. No framework was tried and no comparison or benchmark was run.

- **The model never chooses the next step.** The control flow is the three fixed generators above. The model's outputs are limited
  to one intent from a list, an explanation of supplied facts, events copied from an article, or one option from a set that code
  computed. The agent loops, model-chosen tools, memory and agent-to-agent hand-offs that agent frameworks provide have no place in
  that flow.
- **Each agent is one structured call, checks and a fallback.** That shared logic is `base.run_grounded`; all of `app/agents/base.py` is 93 lines. The
  checks that matter are domain checks: fact citations, numbers that must appear in facts, allowed options, rules re-checked in
  `plan_alert`, and dates that must be written in the article. They are written against this project's `Fact` and rules code.
- **One door to the model.** `app/llm/client.py` is the only file that calls a model, with three backends (`meta`, `openai_compat`,
  `mock`). The test suite forces `mock` (`tests/conftest.py`), so all 679 tests run offline with no key.
- **Size.** The agents, the client, the output schemas and the pipelines are 1,129 lines (`wc -l` over `app/agents/`,
  `app/llm/`, `app/schemas/` and `app/pipelines.py`).

## 5. Gaps: untested or assumed

- **Dependency versions are not pinned.** There is no lockfile, so a fresh install can get different versions from those listed in
  section 1.
- **Client error paths have no test.**
  - The repair attempt in `complete_json` (a second request after a validation error).
  - The 429 backoff with `Retry-After`.
  - The `finish_reason == "length"` error.

  A search of `tests/` for those code paths finds no test.
- **`openai_compat` has never run against a real host.** Only its configuration checks are tested.
- **`stream_text` is unused by the app.** It is tested only with the mock backend.
- **The market agent's scan has no overall time limit.** The call has no 20 s `AgentCall` bound: one scan can make up to 10
  model calls in a row, each with a 20 s request timeout and up to 5 attempts on 429.
- **The number check does not check meaning.** `check_grounding` only checks that each number in the text appears somewhere in the
  facts (in an id, SKU, label, value or date) and that the citations exist. It does not check that a number is used with the fact it
  came from, or check claims made in words.
- **Injection detection is a fixed list of regular expressions** (`app/core/untrusted.py`). Wording outside those patterns is not
  flagged. The article is still sent only as wrapped data and the model's output is still checked, but how well that resists
  injection against the live model has only been checked by the poisoned articles in the tests (mock backend) and by one injection-like chat question in the journey
  script (which never reaches a model), not in general.
- **The router's cache key ignores the stock list.** It is the question text only, up to 300 characters, and the SKU set is not part
  of it. A cached route can name a SKU that is no longer in stock; `sku_question` then answers with an error event. This is not
  tested.
- **The in-memory cache is lost on restart.** Stored plans and briefs are in SQLite and survive.
- **Live model behaviour is shown only by the three journey runs** in `docs/JOURNEY_META_FINAL.md`. The cause of the daily brief
  fallback in run 1 was not captured. No open-weights model has been run.
- **The Meta API behaviours in the client docstring are UNVERIFIED** (section 3).
- **The launch tracker has no model call.** It is code over stored news (`app/watch/tracker.py`, `app/core/launch_state.py`) and is
  not an agent.
