"""Named pipelines: a fixed allowlist, each a hard-coded sequence of steps in code.

  launch_clear   stored market event -> inventory facts -> Inventory agent -> alert-first reply;
                 the Recommender runs only after the owner has asked for help
  sku_question   validate the SKU -> its facts -> Inventory agent -> reply
  daily_brief    shop facts -> Daily agent -> stored brief

Agents never call each other and never choose tools. The Router only returns an intent from a fixed list;
code maps it to a pipeline and checks the SKU. Independent steps run in parallel. Every agent call has a 20 s
limit and uses its deterministic fallback on timeout or failure. Results are cached by a hash of their facts.
Cards are built by code from stored rows; the model never writes a card. Pipelines are generators of events:
status, tool, token, cards, done, error.
"""

import hashlib
import json
import re
import sqlite3
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date
from threading import Lock
from typing import Callable, Iterator

from app.agents import daily, inventory, recommender, router
from app.agents.base import AgentResult
from app.core.explain import money
from app.core.thresholds import Thresholds
from app.core.untrusted import detect_injection
from app.facts import item_facts, summary_facts
from app.llm.client import LLMClient
from app.schemas.facts import Fact
from app.schemas.outputs import NOT_ENOUGH_DATA, DailyBrief, Recommendation, RouteDecision
from app.storage import repo, repo_ai

AGENT_TIMEOUT_S = 20.0
PIPELINES = ("launch_clear", "sku_question", "daily_brief")        # the only pipelines that exist
PIPELINE_FOR_INTENT = {"launch_risk": "launch_clear", "plan_request": "launch_clear", "stock_question": "sku_question", "daily": "daily_brief"}
ASK_PROMPT = "Want me to plan what to do with them?"
CLARIFY = ("I can help with three things: why an item is stuck (give me its SKU, for example TV-SAM-0042), what a newer launch could mean "
           "for your stock, or today's summary. What would you like to know?")
_SKU_LIKE = re.compile(r"\b[A-Za-z]{2}-[A-Za-z0-9]{2,5}-\d{3,5}\b")


# ------------------------------------------------------------------------------ plumbing
class ResultCache:
    """Agent results keyed by a hash of their facts, so the same question returns instantly. Fallbacks are never cached."""

    def __init__(self, size: int = 512):
        self._data: OrderedDict[str, AgentResult] = OrderedDict()
        self._size, self._lock = size, Lock()
        self.hits = self.misses = 0

    def get(self, key: str) -> AgentResult | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return None

    def put(self, key: str, value: AgentResult) -> None:
        if value.source not in ("model", "mock"):
            return
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._size:
                self._data.popitem(last=False)


@dataclass
class Ctx:
    conn: sqlite3.Connection
    shop_id: int
    th: Thresholds
    as_of: date
    llm: LLMClient
    cache: ResultCache
    executor: ThreadPoolExecutor
    timeout_s: float = AGENT_TIMEOUT_S
    timings: list = field(default_factory=list)   # (agent, source, ms, cached): for the done event and the latency report


def facts_hash(agent: str, facts: list[Fact], extra: str = "") -> str:
    payload = json.dumps({"agent": agent, "facts": [f.model_dump() for f in facts], "extra": extra}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AgentCall:
    """One agent call started now and collected later, so independent calls overlap."""

    def __init__(self, ctx: Ctx, name: str, run: Callable[[], AgentResult], fallback: Callable[[], AgentResult], facts: list[Fact], extra: str = ""):
        self.ctx, self.name, self.fallback = ctx, name, fallback
        self.key = facts_hash(name, facts, extra)
        self.started = time.perf_counter()
        self.cached = ctx.cache.get(self.key)
        self.future: Future | None = None if self.cached else ctx.executor.submit(run)

    def result(self) -> AgentResult:
        if self.cached is not None:
            res, was_cached = self.cached, True
        else:
            was_cached = False
            remaining = max(0.0, self.ctx.timeout_s - (time.perf_counter() - self.started))
            try:
                res = self.future.result(timeout=remaining)
            except FutureTimeout:
                res = AgentResult(self.fallback().output, "fallback", [f"timed out after {self.ctx.timeout_s:g}s"])
            except Exception as e:  # noqa: BLE001 - an agent must never take the whole answer down
                res = AgentResult(self.fallback().output, "fallback", [f"failed: {type(e).__name__}"])
            self.ctx.cache.put(self.key, res)
        self.ctx.timings.append((self.name, res.source, round(1000 * (time.perf_counter() - self.started)), was_cached))
        return res


# ------------------------------------------------------------------------------ events
def ev(event_name: str, /, **data) -> dict:
    return {"event": event_name, "data": data}


def tokens(text: str, words: int = 5) -> Iterator[dict]:
    """The validated reply, streamed in small pieces. Text is checked before it is shown, so raw model tokens are never streamed."""
    parts = text.split(" ")
    for i in range(0, len(parts), words):
        yield ev("token", text=" ".join(parts[i:i + words]) + (" " if i + words < len(parts) else ""))


def _strip_marks(text: str) -> str:
    return re.sub(r"\s*\[[A-Za-z0-9:_\-.]+\]", "", text).strip()


def done(ctx: Ctx, pipeline: str | None, started: float, **extra) -> dict:
    mar = repo.money_at_risk(ctx.conn, ctx.shop_id)
    return ev("done", pipeline=pipeline, total_ms=round(1000 * (time.perf_counter() - started)), money_at_risk_cents=mar["money_at_risk_cents"],
              agents=[{"agent": a, "source": s, "ms": ms, "cached": c} for a, s, ms, c in ctx.timings], **extra)


def _item_card(row, explanation: str, th: Thresholds) -> dict:
    from app.core.explain import why_flagged
    return {"type": "item", "sku": row["sku"], "name": f"{row['brand']} {row['model']}", "shelf": row["qty"], "status": row["status"],
            "cap_cents": row["capital_cents"], "dsls": row["dsls"], "why": why_flagged(row["status"], row["dsls"], th.categories[row["category"]].slow_dsls),
            "explanation": explanation}


# ------------------------------------------------------------------------------ agent steps
def _inventory_call(ctx: Ctx, row) -> AgentCall:
    facts = item_facts(row, ctx.th, ctx.as_of)
    sku = row["sku"]
    return AgentCall(ctx, "inventory", lambda: inventory.explain(ctx.llm, sku, facts), lambda: AgentResult(inventory._fallback(sku, facts), "fallback"), facts)


def _recommender_call(ctx: Ctx, row) -> AgentCall:
    facts = item_facts(row, ctx.th, ctx.as_of)
    sku = row["sku"]
    return AgentCall(ctx, "recommender", lambda: recommender.recommend(ctx.llm, sku, facts, help_requested=True),
                     lambda: AgentResult(recommender._fallback(sku, facts), "fallback"), facts)


# ------------------------------------------------------------------------------ pipelines
def launch_clear(ctx: Ctx, started: float) -> Iterator[dict]:
    yield ev("status", message="Checking your stored launch news")
    groups = repo.launch_exposed(ctx.conn, ctx.shop_id, ctx.th)
    if not groups:
        yield from tokens(f"{NOT_ENOUGH_DATA}: none of your launch-family items is dead or slow right now.")
        yield ev("cards", cards=[])
        yield done(ctx, "launch_clear", started, intent_note="no exposed items")
        return
    g = groups[0]
    yield ev("tool", name="stored_market_event", source=g["source"])
    rows = [repo_ai.item_row(ctx.conn, ctx.shop_id, t["sku"]) for t in g["top"]]
    yield ev("tool", name="inventory_facts", items=len(rows), cap_cents=sum(r["capital_cents"] for r in rows))
    yield ev("status", message="Reading the numbers for your top items")
    calls = [_inventory_call(ctx, r) for r in rows]            # started together, collected in order
    explained = []
    for row, call in zip(rows, calls):
        res = call.result()
        yield ev("tool", name="inventory_agent", sku=row["sku"], source=res.source)
        explained.append((row, _strip_marks(res.output.summary)))

    cap = money(g["cap_cents"])
    src = g["source"]
    when = (f" Source: {src['name']}, published {str(src['published'])[:10]}. Launch date: {src['launch_date']} (as written in the article)."
            if src["attached"] and src["launch_date"] else
            f" Source: {src['name']}, published {str(src['published'])[:10]}. Announced, no launch date given in the article."
            if src["attached"] else f" {g['source_note']}")
    if src["attached"] and src["freshness"] != "fresh":
        old_note = f"{src['age_days']} days old" if src["age_days"] is not None else "of unknown age"
        when += f" Caution: this source is {old_note}, so it is not fresh news."
    text = f"{g['title']}. {g['n']} of your {g['label']} are dead or slow, with {cap} tied up.{when}"
    for row, summary in explained:
        text += f" {row['brand']} {row['model']}: {summary}"
    help_given = g["help_requested"]
    if not help_given:                                          # alert first: ask, and stop
        yield from tokens(f"{text} {ASK_PROMPT}")
        yield ev("cards", cards=[{"type": "alert", "alert_id": g["alert_id"], "title": g["title"], "n": g["n"], "cap_cents": g["cap_cents"],
                                  "source": src, "help_requested": False, "question": ASK_PROMPT}] + [_item_card(r, s, ctx.th) for r, s in explained])
        yield done(ctx, "launch_clear", started, alert_id=g["alert_id"], help_requested=False)
        return

    yield ev("status", message="You asked for help, so I am planning one option per item")
    repo.plan(ctx.conn, ctx.shop_id, g["alert_id"], len(rows), ctx.th)           # the rule-based plan is the floor
    rec_calls = [(r, _recommender_call(ctx, r)) for r in rows]                   # the Recommender runs only now, after help
    for row, call in rec_calls:
        res = call.result()
        rec: Recommendation = res.output
        repo_ai.apply_agent_recommendation(ctx.conn, ctx.shop_id, g["alert_id"], row["sku"], rec.option, rec.discount_pct, rec.reasons, res.source)
        yield ev("tool", name="recommender_agent", sku=row["sku"], source=res.source, option=rec.option)
    plans = repo.plan(ctx.conn, ctx.shop_id, g["alert_id"], len(rows), ctx.th)
    yield from tokens(f"{text} Here is one option for each item. You decide: nothing changes until you accept.")
    yield ev("cards", cards=[{"type": "plan", **p} for p in plans])
    yield done(ctx, "launch_clear", started, alert_id=g["alert_id"], help_requested=True)


def sku_question(ctx: Ctx, started: float, sku: str) -> Iterator[dict]:
    yield ev("status", message=f"Looking up {sku}")
    row = repo_ai.item_row(ctx.conn, ctx.shop_id, sku)
    if row is None:
        yield ev("error", message=f"I can't find an item with SKU {sku} in your stock.")
        yield done(ctx, "sku_question", started)
        return
    yield ev("tool", name="inventory_facts", sku=sku)
    res = _inventory_call(ctx, row).result()
    yield ev("tool", name="inventory_agent", sku=sku, source=res.source)
    summary = _strip_marks(res.output.summary)
    yield from tokens(summary)
    yield ev("cards", cards=[_item_card(row, summary, ctx.th)])
    yield done(ctx, "sku_question", started, sku=sku)


def daily_brief(ctx: Ctx, started: float) -> Iterator[dict]:
    yield ev("status", message="Working out today's numbers")
    summary = repo.summary(ctx.conn, ctx.shop_id)
    if summary is None:
        yield from tokens(f"{NOT_ENOUGH_DATA}: no stock list has been uploaded yet.")
        yield ev("cards", cards=[])
        yield done(ctx, "daily_brief", started)
        return
    facts = summary_facts(summary, repo.launch_exposed(ctx.conn, ctx.shop_id, ctx.th))
    yield ev("tool", name="shop_facts", facts=len(facts))
    res = AgentCall(ctx, "daily", lambda: daily.brief(ctx.llm, facts), lambda: AgentResult(daily._fallback(facts), "fallback"), facts).result()
    brief: DailyBrief = res.output
    yield ev("tool", name="daily_agent", source=res.source)
    repo_ai.save_brief(ctx.conn, ctx.shop_id, facts_hash("daily", facts), res.source, brief.headline, brief.bullets, brief.citations)
    sentences = [_strip_marks(s).rstrip(" .") + "." for s in [brief.headline, *brief.bullets] if _strip_marks(s)]
    yield from tokens(" ".join(sentences))
    t = summary["totals"]
    yield ev("cards", cards=[{"type": "summary", "money_at_risk_cents": summary["money_at_risk_cents"], "at_risk_all_cents": summary["at_risk_all_cents"],
                              "dead": t["dead"], "slow": t["slow"], "stock_at_cost_cents": t["stock_at_cost_cents"]}])
    yield done(ctx, "daily_brief", started)


# ------------------------------------------------------------------------------ routing
def choose(ctx: Ctx, question: str, skus: set[str]) -> RouteDecision:
    """Code decides. Text that looks like instructions is never classified by a model; otherwise the Router (bounded by the
    agent timeout) returns only an intent, and the SKU must exist."""
    if detect_injection(question):
        return RouteDecision(intent="unclear", agent="none", sku="")
    call = AgentCall(ctx, "router", lambda: AgentResult(router.route(ctx.llm, question, skus), "mock" if ctx.llm.is_mock else "model"),
                     lambda: AgentResult(router._keyword_route(question, skus), "fallback"), [], extra=question[:300])
    return call.result().output


def answer(ctx: Ctx, question: str, sku: str | None = None) -> Iterator[dict]:
    started = time.perf_counter()
    yield ev("status", message="Reading your question")        # goes out before any model call
    skus = repo_ai.known_skus(ctx.conn, ctx.shop_id)
    mentioned = {m.group(0).upper() for m in _SKU_LIKE.finditer(question)}
    unknown = sorted(m for m in mentioned if m not in {s.upper() for s in skus})
    if unknown:
        yield ev("error", message=f"I can't find an item with SKU {unknown[0]} in your stock.")
        yield done(ctx, None, started)
        return
    if sku:
        sku = next((s for s in skus if s.upper() == sku.upper()), None)
    decision = RouteDecision(intent="stock_question", agent="inventory", sku=sku) if sku else choose(ctx, question, skus)
    yield ev("tool", name="router", intent=decision.intent)
    pipeline = PIPELINE_FOR_INTENT.get(decision.intent)
    if pipeline not in PIPELINES:                                # unknown or unsafe: ask, never guess
        yield from tokens(CLARIFY)
        yield ev("cards", cards=[])
        yield done(ctx, None, started, intent=decision.intent)
        return
    if pipeline == "sku_question":
        target = decision.sku or next((s for s in skus if s.upper() in {m for m in mentioned}), "")
        if not target:
            yield from tokens("Which item do you mean? Tell me its SKU, for example TV-SAM-0042.")
            yield ev("cards", cards=[])
            yield done(ctx, None, started, intent=decision.intent)
            return
        yield from sku_question(ctx, started, target)
    elif pipeline == "launch_clear":
        yield from launch_clear(ctx, started)
    else:
        yield from daily_brief(ctx, started)
