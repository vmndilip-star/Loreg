"""Shared plumbing for agents: one grounded model call with a deterministic fallback.

An agent never decides numbers. Code supplies Facts; the model may only explain them. After
the model answers, code checks (1) every citation is a real fact id, (2) every number in the
text appears in a fact and (3) text with no citation starts with "Not enough data". If a check
fails the model is asked once more, with the problems listed; if the second answer fails too, or
the model is unavailable, the deterministic fallback is returned instead, so the screen is still
correct without a model.

Inventory, Recommender and Daily go through run_grounded. The Router writes no prose (it returns
an intent from a fixed list, and code picks the agent and checks the SKU) and the Market agent
cites no facts (code checks each quote and date word for word against the article), so each has
its own code checks instead (app/agents/router.py, app/agents/market.py `_enforce`).
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, TypeVar

from pydantic import BaseModel

from app.llm.client import LLMClient, LLMError
from app.schemas.facts import Fact, render_facts
from app.schemas.outputs import NOT_ENOUGH_DATA

T = TypeVar("T", bound=BaseModel)

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

GROUNDING_RULES = (
    "Use only the facts listed under FACTS. List the id of every fact you use in the citations field, "
    "written exactly as shown, without brackets. Never state a number that is not in a fact. "
    f"If the facts are not enough, say \"{NOT_ENOUGH_DATA}\" and cite nothing. "
    "The shop owner makes the final decision; you only explain and suggest."
)


@dataclass
class AgentResult:
    output: BaseModel
    source: str  # "model", "mock" or "fallback"
    notes: list[str] = field(default_factory=list)
    retried: bool = False  # the model's first answer failed a check and its second one passed

    @property
    def outcome(self) -> str:
        """model_ok | retried_ok | fallback | mock: what the fallback-rate metric counts."""
        if self.source == "fallback":
            return "fallback"
        if self.source == "mock":
            return "mock"
        return "retried_ok" if self.retried else "model_ok"


def _normalize(token: str) -> str:
    return token.replace(",", "").rstrip(".")


def numbers_in(text: str) -> set[str]:
    return {_normalize(m) for m in _NUMBER.findall(text)}


def ungrounded_numbers(text: str, facts: Iterable[Fact], extra_allowed: Iterable[int | float | str] = ()) -> list[str]:
    allowed = {_normalize(str(x)) for x in extra_allowed}
    for f in facts:
        allowed |= numbers_in(f"{f.id} {f.sku} {f.label} {f.value} {f.as_of}")
    return sorted(n for n in numbers_in(text) if n not in allowed)


def check_grounding(text: str, citations: list[str], facts: list[Fact],
                    extra_allowed: Iterable[int | float | str] = ()) -> list[str]:
    """Return a list of problems; empty means the output is grounded."""
    problems = []
    known = {f.id for f in facts}
    bad = [c for c in citations if c not in known]
    if bad:
        problems.append(f"cites facts that do not exist: {bad}")
    if not citations and not text.startswith(NOT_ENOUGH_DATA):
        problems.append("makes claims without citing any fact")
    loose = ungrounded_numbers(text, facts, extra_allowed)
    if loose:
        problems.append(f"states numbers that are in no fact: {loose}")
    return problems


def run_grounded(client: LLMClient, *, system: str, instruction: str, facts: list[Fact],
                 schema: type[T], fallback: Callable[[], T],
                 texts_of: Callable[[T], str], citations_of: Callable[[T], list[str]],
                 extra_allowed: Iterable[int | float | str] = (),
                 extra_checks: Callable[[T], list[str]] | None = None) -> AgentResult:
    user = f"FACTS\n{render_facts(facts)}\n\nTASK\n{instruction}"
    system = f"{system}\n\n{GROUNDING_RULES}"

    def ask(message: str):
        return client.complete_json(system=system, user=message, schema=schema, mock_output=fallback)

    def problems_of(out) -> tuple:
        if hasattr(out, "citations"):  # models often write ids as "[m1]"; the id itself is "m1"
            out = out.model_copy(update={"citations": [c.strip().strip("[]").strip() for c in out.citations]})
        problems = check_grounding(texts_of(out), citations_of(out), facts, extra_allowed)
        if extra_checks:
            problems += extra_checks(out)
        return out, problems

    try:
        out = ask(user)
    except LLMError as e:
        return AgentResult(fallback(), "fallback", [f"model unavailable: {e}"])
    if client.is_mock:
        return AgentResult(out, "mock")
    out, problems = problems_of(out)
    if not problems:
        return AgentResult(out, "model")
    retry = (f"{user}\n\nYOUR LAST ANSWER WAS REJECTED BY CODE\n" + "\n".join(f"- {p}" for p in problems)
             + "\nAnswer again. Follow every rule; use only the FACTS above.")
    try:
        again, problems_again = problems_of(ask(retry))
    except LLMError as e:
        return AgentResult(fallback(), "fallback", problems + [f"retry: model unavailable: {e}"])
    if problems_again:
        return AgentResult(fallback(), "fallback", problems + [f"after one retry: {p}" for p in problems_again])
    return AgentResult(again, "model", [f"first answer rejected: {p}" for p in problems], retried=True)


def facts_for(facts: list[Fact], sku: str) -> list[Fact]:
    return [f for f in facts if f.sku == sku]
