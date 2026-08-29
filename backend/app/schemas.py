from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

TRANSACTION_PATTERN = "^(payment|advance|fee|interest|adjustment|refund_credit|refund_debit)$"
DIRECTION_PATTERN = "^(debit|credit)$"
DAY_COUNT_PATTERN = "^(actual_365|actual_366|actual_actual|30_360)$"
INTEREST_METHOD_PATTERN = "^(daily_simple)$"
PAYMENT_ALLOCATION_PATTERN = "^(fees_interest_principal)$"


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
