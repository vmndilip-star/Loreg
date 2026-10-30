"""Where the money in dead and slow stock stands. Pure function: no I/O, no model.

Two buckets, in integer cents:
  at_risk             dead or slow stock with no accepted plan yet
  action_in_progress  dead or slow stock the owner has accepted a plan for

Accepting a plan moves an item's capital from at_risk to action_in_progress; nothing is "freed" until the units sell, and that is
not something Loreg can see. Invariant: at_risk + action_in_progress is the same before and after any accept, decline or undo.
"""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Exposure:
    at_risk_cents: int
    at_risk_n: int
    action_in_progress_cents: int
    action_in_progress_n: int

    @property
    def total_cents(self) -> int:
        return self.at_risk_cents + self.action_in_progress_cents

    @property
    def total_n(self) -> int:
        return self.at_risk_n + self.action_in_progress_n

    def as_dict(self) -> dict:
        return {"at_risk_cents": self.at_risk_cents, "at_risk_n": self.at_risk_n, "action_in_progress_cents": self.action_in_progress_cents,
                "action_in_progress_n": self.action_in_progress_n, "total_cents": self.total_cents, "total_n": self.total_n}


def compute_exposure(items: Iterable[tuple[str, int]], accepted_skus: Iterable[str]) -> Exposure:
    """`items`: (sku, capital_cents) for every dead or slow item with something on the shelf. `accepted_skus`: SKUs with an accepted plan.
    An accepted SKU that is not in `items` (no longer dead or slow) counts nowhere."""
    accepted = set(accepted_skus)
    risk = moving = risk_n = moving_n = 0
    for sku, cents in items:
        if sku in accepted:
            moving, moving_n = moving + cents, moving_n + 1
        else:
            risk, risk_n = risk + cents, risk_n + 1
    return Exposure(risk, risk_n, moving, moving_n)
