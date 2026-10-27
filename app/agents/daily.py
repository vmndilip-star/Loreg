"""Loreg Daily: the short morning summary, written from facts that code computed."""

from app.agents.base import AgentResult, run_grounded
from app.llm.client import LLMClient
from app.schemas.facts import Fact
from app.schemas.outputs import NOT_ENOUGH_DATA, DailyBrief

SYSTEM = (
    "You write the short morning summary for a small US electronics and appliance shop owner. "
    "Give one headline and at most four short bullets about money tied up in slow and dead stock and "
    "any launch alerts. Do not recommend a discount; alerts come first and the owner asks for help. "
    "Write every bullet as one complete sentence that ends with a full stop. Do not ask questions. Do not write any date."
)
_PRIORITY = ("capital_at_risk_total", "dead_capital", "slow_capital", "launch_exposed_count")


def _fallback(facts: list[Fact]) -> DailyBrief:
    if not facts:
        return DailyBrief(headline=NOT_ENOUGH_DATA, bullets=[], citations=[])
    ranked = sorted(facts, key=lambda f: _PRIORITY.index(f.key) if f.key in _PRIORITY else len(_PRIORITY))
    top = ranked[:5]
    return DailyBrief(headline=f"{top[0].label}: {top[0].value}",
                      bullets=[f"{f.label}: {f.value}" for f in top[1:]],
                      citations=[f.id for f in top])


def brief(client: LLMClient, facts: list[Fact]) -> AgentResult:
    return run_grounded(
        client, system=SYSTEM, facts=facts, schema=DailyBrief,
        instruction="Write today's Loreg Daily.",
        fallback=lambda: _fallback(facts),
        texts_of=lambda o: " ".join([o.headline, *o.bullets]), citations_of=lambda o: o.citations,
        extra_checks=lambda o: [] if len(o.bullets) <= 4 else ["more than four bullets"],
    )
