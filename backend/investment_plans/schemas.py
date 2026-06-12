from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


PlanType = Literal["full_analysis", "recurring_investment"]
RiskProfile = Literal["conservative", "balanced", "aggressive"]
IndustryCycle = Literal["stable", "cyclical", "growth", "unknown"]
ValuationLevel = Literal["undervalued", "fair", "expensive", "overheated", "unknown"]
TrendSignal = Literal["improving", "stable", "weakening", "unknown"]
PlanRecommendation = Literal["buy", "add", "hold", "pause", "take_profit", "risk_review"]
LineFrequency = Literal["daily", "weekly"]


class InvestmentPlanRequest(BaseModel):
    user_id: str = Field(default="guest", min_length=1, max_length=80)
    stock_symbol: str = Field(min_length=1, max_length=20)
    stock_name: str | None = Field(default=None, max_length=80)
    plan_type: PlanType
    current_price: float = Field(gt=0)
    monthly_amount: float | None = Field(default=None, ge=0)
    initial_amount: float = Field(default=0, ge=0)
    investment_years: int = Field(default=5, ge=1, le=40)
    risk_profile: RiskProfile = "balanced"
    max_loss_percent: float = Field(default=25, ge=1, le=80)
    target_return_percent: float = Field(default=50, ge=1, le=500)
    average_cost: float | None = Field(default=None, gt=0)
    shares_owned: float = Field(default=0, ge=0)
    industry_cycle: IndustryCycle = "unknown"
    valuation_level: ValuationLevel = "unknown"
    allow_high_volatility: bool = True
    tracked_materials: list[str] = Field(default_factory=list)
    public_event_keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_plan_inputs(self) -> "InvestmentPlanRequest":
        if self.plan_type == "recurring_investment" and not self.monthly_amount:
            raise ValueError("recurring_investment requires monthly_amount greater than 0")
        return self


class AllocationPlan(BaseModel):
    monthly_amount: float | None = None
    fixed_buy_ratio: float
    cash_reserve_ratio: float
    monthly_fixed_buy_amount: float | None = None
    monthly_cash_reserve_amount: float | None = None
    estimated_monthly_shares: float | None = None
    total_planned_contribution: float


class PriceBand(BaseModel):
    name: str
    price: float
    action: str


class ActionRule(BaseModel):
    trigger: str
    action: str


class InvestmentProjection(BaseModel):
    invested_principal: float
    estimated_shares_from_monthly_plan: float | None = None
    break_even_price: float | None = None
    target_take_profit_price: float | None = None
    max_loss_review_price: float | None = None


class InvestmentPlanRecord(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    request: InvestmentPlanRequest
    title: str
    summary: str
    suitability: str
    allocation: AllocationPlan
    price_bands: list[PriceBand] = Field(default_factory=list)
    action_rules: list[ActionRule] = Field(default_factory=list)
    tracking_indicators: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    next_review_items: list[str] = Field(default_factory=list)
    projection: InvestmentProjection
    disclosure: str


class PlanReviewRequest(BaseModel):
    current_price: float = Field(gt=0)
    average_cost: float | None = Field(default=None, gt=0)
    shares_owned: float = Field(default=0, ge=0)
    available_cash: float = Field(default=0, ge=0)
    revenue_trend: TrendSignal = "unknown"
    earnings_trend: TrendSignal = "unknown"
    material_cost_trend: TrendSignal = "unknown"
    global_peer_trend: TrendSignal = "unknown"
    public_event_signal: TrendSignal = "unknown"
    valuation_level: ValuationLevel = "unknown"
    notes: str | None = Field(default=None, max_length=500)


class PlanReviewRecord(BaseModel):
    id: UUID
    plan_id: UUID
    created_at: datetime
    request: PlanReviewRequest
    recommendation: PlanRecommendation
    recommendation_label: str
    summary: str
    suggested_action_amount: float
    suggested_cash_reserve: float
    estimated_shares_to_buy: float | None = None
    price_position: str
    risk_flags: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class LineSubscriptionRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=80)
    line_user_id: str | None = Field(default=None, max_length=120)
    plan_id: UUID | None = None
    frequency: LineFrequency = "daily"
    consent: bool = True


class LineSubscriptionRecord(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    user_id: str
    line_user_id: str | None = None
    plan_id: UUID | None = None
    frequency: LineFrequency
    consent: bool
    status: str = "pending_binding"
