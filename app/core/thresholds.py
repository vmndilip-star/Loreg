"""Load the rule thresholds from config/thresholds.yaml. The only file in app/core that reads a file."""

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "thresholds.yaml"


@dataclass(frozen=True)
class CategoryThresholds:
    slow_dsls: float
    dead_dsls: float
    slow_days_of_stock: float
    new_grace_days: float


@dataclass(frozen=True)
class Thresholds:
    rule_version: int
    categories: dict
    seasonal_multiplier: float
    velocity_outlier_floor: int
    velocity_outlier_iqr_multiplier: float
    msrp_mismatch_factor: float
    discount_ladder_pct: tuple
    below_cost_dead_multiple: float
    below_cost_max_pct: int
    holding_cost_rate: float
    generator: dict

    def for_category(self, category: str, seasonal: bool = False) -> CategoryThresholds:
        """Thresholds for a category; seasonal items get slow, dead and days-of-stock x multiplier (not grace)."""
        c = self.categories[category]
        if not seasonal:
            return c
        m = self.seasonal_multiplier
        return CategoryThresholds(c.slow_dsls * m, c.dead_dsls * m, c.slow_days_of_stock * m, c.new_grace_days)


def load_thresholds(path: Path | str | None = None) -> Thresholds:
    raw = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    dq, act = raw["data_quality"], raw["actions"]
    return Thresholds(
        rule_version=int(raw["rule_version"]),
        categories={k: CategoryThresholds(float(v["slow_dsls"]), float(v["dead_dsls"]),
                                          float(v["slow_days_of_stock"]), float(v["new_grace_days"]))
                    for k, v in raw["categories"].items()},
        seasonal_multiplier=float(raw["seasonal_multiplier"]),
        velocity_outlier_floor=int(dq["velocity_outlier_floor_units_30d"]),
        velocity_outlier_iqr_multiplier=float(dq["velocity_outlier_iqr_multiplier"]),
        msrp_mismatch_factor=float(dq["msrp_mismatch_factor"]),
        discount_ladder_pct=tuple(int(p) for p in act["discount_ladder_pct"]),
        below_cost_dead_multiple=float(act["below_cost_dead_multiple"]),
        below_cost_max_pct=int(act["below_cost_max_pct"]),
        holding_cost_rate=float(act["holding_cost_rate"]),
        generator=raw["generator"],
    )
