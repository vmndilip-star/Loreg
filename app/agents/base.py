"""Shared plumbing for agents: one grounded model call with a deterministic fallback.

An agent never decides numbers. Code supplies Facts; the model may only explain them. After
the model answers, code checks (1) every citation is a real fact id and (2) every number in the
text appears in a fact. If anything fails, or the model is unavailable, the deterministic
fallback is returned instead, so the screen is still correct without a model.
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
    try:
        out = client.complete_json(system=f"{system}\n\n{GROUNDING_RULES}", user=user,
                                   schema=schema, mock_output=fallback)
    except LLMError as e:
        return AgentResult(fallback(), "fallback", [f"model unavailable: {e}"])
    if client.is_mock:
        return AgentResult(out, "mock")
    if hasattr(out, "citations"):  # models often write ids as "[m1]"; the id itself is "m1"
        out = out.model_copy(update={"citations": [c.strip().strip("[]").strip() for c in out.citations]})
    problems = check_grounding(texts_of(out), citations_of(out), facts, extra_allowed)
    if extra_checks:
        problems += extra_checks(out)
    if problems:
        return AgentResult(fallback(), "fallback", problems)
    return AgentResult(out, "model")


def facts_for(facts: list[Fact], sku: str) -> list[Fact]:
    return [f for f in facts if f.sku == sku]
