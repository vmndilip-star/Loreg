"""Facts: the only things an agent may talk about. Code computes them; agents cite them."""

from typing import Literal

from pydantic import BaseModel


class Fact(BaseModel):
    """One checkable fact, produced by app.core (a metric) or by a fetched source.

    `id` is what an agent cites. `value` is the display text, already formatted by code.
    `key` and `number` let code make decisions without parsing text.
    """

    id: str
    kind: Literal["metric", "source"] = "metric"
    sku: str = ""
    key: str = ""
    label: str
    value: str
    number: float | None = None
    as_of: str = ""
    url: str = ""

    def line(self) -> str:
        tail = f" (as of {self.as_of})" if self.as_of else ""
        tail += f" <{self.url}>" if self.url else ""
        return f"[{self.id}] {self.label}: {self.value}{tail}"


def render_facts(facts: list[Fact]) -> str:
    return "\n".join(f.line() for f in facts) if facts else "(no facts available)"
