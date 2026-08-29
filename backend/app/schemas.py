from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

TRANSACTION_PATTERN = "^(payment|advance|fee|interest|adjustment|refund_credit|refund_debit)$"
DIRECTION_PATTERN = "^(debit|credit)$"
DAY_COUNT_PATTERN = "^(actual_365|actual_366|actual_actual|30_360)$"
INTEREST_METHOD_PATTERN = "^(daily_simple)$"
PAYMENT_ALLOCATION_PATTERN = "^(fees_interest_principal)$"
PLAN_STRATEGY_PATTERN = "^(priority_rollover)$"
PLAN_STATUS_PATTERN = "^(active|paused|archived)$"
SCENARIO_STATUS_PATTERN = "^(active|archived)$"
SCENARIO_CHANGE_PATTERN = "^(budget_delta|budget_override|lump_sum|payment_holiday|base_payment_override|priority_override|interest_rate)$"


class TransactionCreate(BaseModel):
    effective_date: date
    transaction_type: str = Field(pattern=TRANSACTION_PATTERN)
    amount: Decimal = Field(gt=0)
    direction: str | None = Field(default=None, pattern=DIRECTION_PATTERN)
    note: str = ""
    reference: str = ""
    source: str = "manual"
    reason: str = ""


class TransactionCorrection(BaseModel):
    effective_date: date
    transaction_type: str = Field(pattern=TRANSACTION_PATTERN)
    amount: Decimal = Field(gt=0)
    direction: str | None = Field(default=None, pattern=DIRECTION_PATTERN)
    note: str = ""
    reason: str = Field(min_length=3)


class TransactionReverse(BaseModel):
    reason: str = Field(min_length=3)
    effective_date: date | None = None


class InterestRateCreate(BaseModel):
    effective_from: date
    annual_rate: Decimal = Field(ge=0, le=100)
    day_count_convention: str = Field(default="actual_365", pattern=DAY_COUNT_PATTERN)
    reason: str = Field(min_length=3)


class AccountInterestSettingsUpdate(BaseModel):
    interest_method: str = Field(default="daily_simple", pattern=INTEREST_METHOD_PATTERN)
    day_count_convention: str = Field(default="actual_365", pattern=DAY_COUNT_PATTERN)
    payment_allocation: str = Field(default="fees_interest_principal", pattern=PAYMENT_ALLOCATION_PATTERN)
    reason: str = Field(min_length=3)


class PaymentPlanMemberInput(BaseModel):
    account_id: int = Field(gt=0)
    priority: int = Field(ge=1)
    base_payment: Decimal = Field(ge=0)
    enabled: bool = True


class PaymentPlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    first_payment_date: date
    monthly_budget: Decimal = Field(default=Decimal("0.00"), ge=0)
    strategy: str = Field(default="priority_rollover", pattern=PLAN_STRATEGY_PATTERN)
    status: str = Field(default="active", pattern=PLAN_STATUS_PATTERN)
    notes: str = ""
    members: list[PaymentPlanMemberInput] = Field(min_length=1)


class PaymentPlanUpdate(PaymentPlanCreate):
    reason: str = Field(min_length=3)


class ScenarioChangeInput(BaseModel):
    change_type: str = Field(pattern=SCENARIO_CHANGE_PATTERN)
    account_id: int | None = Field(default=None, gt=0)
    effective_from: date
    effective_to: date | None = None
    value: Decimal | None = None
    day_count_convention: str = Field(default="actual_365", pattern=DAY_COUNT_PATTERN)
    note: str = ""


class ScenarioCreate(BaseModel):
    plan_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=180)
    description: str = ""
    status: str = Field(default="active", pattern=SCENARIO_STATUS_PATTERN)
    changes: list[ScenarioChangeInput] = Field(default_factory=list)


class ScenarioUpdate(ScenarioCreate):
    reason: str = Field(min_length=3)
