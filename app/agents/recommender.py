"""Recommender agent: one proposed action per item. The owner makes the final call.

Code decides which options are allowed (for example "return" only while the supplier's return
window is open, and discounts only up to a cap computed from cost). The model picks one allowed
option and explains it from the facts. A proposal is never applied: status stays "proposed".
The alert-first rule is enforced here: no plan is produced until the owner has asked for help.
"""

from app.agents.base import AgentResult, facts_for, run_grounded
from app.llm.client import LLMClient
from app.schemas.facts import Fact
from app.schemas.outputs import DISCOUNT_LADDER, NOT_ENOUGH_DATA, Recommendation

SYSTEM = (
    "You help a small US electronics and appliance shop owner decide what to do with one slow or dead "
    "stock item. Choose exactly ONE option from the allowed options and give one to three short reasons. "
    "Present it as a suggestion; the owner decides. If the option is not 'discount', discount_pct must be 0."
)


class AlertFirstError(PermissionError):
    """A plan was requested before the owner asked for help."""


def _flag(facts: list[Fact], key: str) -> bool:
    return any(f.key == key and f.number == 1 for f in facts)


def allowed_discounts(facts: list[Fact]) -> list[int]:
    caps = [f.number for f in facts if f.key == "max_discount_pct" and f.number is not None]
    cap = min(caps) if caps else max(DISCOUNT_LADDER)
    return [p for p in DISCOUNT_LADDER if p <= cap]


def allowed_options(facts: list[Fact]) -> list[str]:
    options = ["bundle", "hold"]
    if allowed_discounts(facts):
        options.insert(0, "discount")
    if _flag(facts, "return_window_open"):
        options.insert(0, "return")
    return options


def _fallback(sku: str, facts: list[Fact]) -> Recommendation:
    if not facts:
        return Recommendation(sku=sku, option="hold", discount_pct=0,
                              reasons=[NOT_ENOUGH_DATA], citations=[])
    options, pcts = allowed_options(facts), allowed_discounts(facts)
    if "return" in options:
        option, pct = "return", 0
    elif "discount" in options and _flag(facts, "status_dead"):
        option, pct = "discount", min(p for p in pcts if p >= min(10, max(pcts))) if pcts else 0
    elif "discount" in options:
        option, pct = "discount", min(pcts)
    else:
        option, pct = "hold", 0
    cited = facts[:3]
    return Recommendation(sku=sku, option=option, discount_pct=pct,
                          reasons=[f"{f.label}: {f.value}" for f in cited],
                          citations=[f.id for f in cited])


def recommend(client: LLMClient, sku: str, facts: list[Fact], *, help_requested: bool) -> AgentResult:
    if not help_requested:
        raise AlertFirstError("Loreg only plans after the owner asks for help")
    own = facts_for(facts, sku)
    options, pcts = allowed_options(own), allowed_discounts(own)

    def checks(o: Recommendation) -> list[str]:
        problems = []
        if o.sku != sku:
            problems.append(f"answered for {o.sku}, not {sku}")
        if o.option not in options:
            problems.append(f"option {o.option!r} is not allowed (allowed: {options})")
        if o.option == "discount" and o.discount_pct not in pcts:
            problems.append(f"discount {o.discount_pct}% is not allowed (allowed: {pcts})")
        return problems

    if not own:
        return AgentResult(_fallback(sku, own), "fallback", ["no facts for this SKU"])
    return run_grounded(
        client, system=SYSTEM, facts=own, schema=Recommendation,
        instruction=(f"Propose one action for SKU {sku}. Set sku to {sku} and status to 'proposed'. "
                     f"Allowed options: {options}. Allowed discount percentages: {pcts or 'none'}."),
        fallback=lambda: _fallback(sku, own),
        texts_of=lambda o: " ".join(o.reasons), citations_of=lambda o: o.citations,
        extra_allowed=pcts, extra_checks=checks,
    )
