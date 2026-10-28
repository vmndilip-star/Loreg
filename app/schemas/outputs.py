"""Validated outputs of the agents. Every field is required so the model can be held to a strict schema."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

NOT_ENOUGH_DATA = "Not enough data"

Option = Literal["discount", "bundle", "return", "hold"]
DISCOUNT_LADDER = (5, 10, 15, 20)  # percentages the recommender may propose; must match config/thresholds.yaml


class InventoryExplanation(BaseModel):
    sku: str
    summary: str
    citations: list[str]


class LaunchEvent(BaseModel):
    brand: str
    product_family: str
    date_stated: bool
    launch_date: str = Field(description="Exactly as written in the article, or empty if none is given")
    quote: str = Field(description="Short quote from the article that supports this")


class MarketExtraction(BaseModel):
    """What the model may return for an article: data only, never instructions."""

    events: list[LaunchEvent]


class MarketReading(BaseModel):
    source_id: str
    flagged: bool  # the text contained instruction-like content and was treated as data only
    flags: list[str]
    events: list[LaunchEvent]


class Recommendation(BaseModel):
    sku: str
    option: Option
    discount_pct: int
    reasons: list[str]
    citations: list[str]
    status: Literal["proposed"] = "proposed"  # only the owner can accept or decline

    @model_validator(mode="after")
    def _discount_only_with_discount(self):
        if self.option == "discount" and self.discount_pct not in DISCOUNT_LADDER:
            raise ValueError(f"discount_pct must be one of {DISCOUNT_LADDER}")
        if self.option != "discount" and self.discount_pct != 0:
            raise ValueError("discount_pct must be 0 unless the option is discount")
        return self


class DailyBrief(BaseModel):
    headline: str
    bullets: list[str]
    citations: list[str]


Intent = Literal["stock_question", "launch_risk", "plan_request", "daily", "unknown_fact", "unclear"]
AgentName = Literal["inventory", "market", "recommender", "daily", "none"]


class RouteDecision(BaseModel):
    intent: Intent
    agent: AgentName
    sku: str
