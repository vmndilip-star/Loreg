"""Inventory agent: explains why a SKU is stalled, using only numbers computed in app.core."""

from app.agents.base import AgentResult, facts_for, run_grounded
from app.llm.client import LLMClient
from app.schemas.facts import Fact
from app.schemas.outputs import NOT_ENOUGH_DATA, InventoryExplanation

SYSTEM = (
    "You are the inventory analyst for a small US electronics and appliance shop. "
    "Explain in two or three plain sentences why this item's stock is tied up, for the shop owner. "
    "Do not recommend a price change or any action."
)


def _fallback(sku: str, facts: list[Fact]) -> InventoryExplanation:
    if not facts:
        return InventoryExplanation(sku=sku, summary=NOT_ENOUGH_DATA, citations=[])
    parts = "; ".join(f"{f.label} {f.value}" for f in facts)
    return InventoryExplanation(sku=sku, summary=f"{sku}: {parts}.", citations=[f.id for f in facts])


def explain(client: LLMClient, sku: str, facts: list[Fact]) -> AgentResult:
    own = facts_for(facts, sku)
    if not own:
        return AgentResult(_fallback(sku, own), "fallback", ["no facts for this SKU"])
    return run_grounded(
        client, system=SYSTEM, facts=own, schema=InventoryExplanation,
        instruction=f"Explain the stock position of SKU {sku}. Set the sku field to {sku}.",
        fallback=lambda: _fallback(sku, own),
        texts_of=lambda o: o.summary, citations_of=lambda o: o.citations,
        extra_checks=lambda o: [] if o.sku == sku else [f"answered for {o.sku}, not {sku}"],
    )
