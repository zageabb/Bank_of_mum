from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

TRANSACTION_PATTERN = "^(payment|advance|fee|interest|adjustment|refund_credit|refund_debit)$"
DIRECTION_PATTERN = "^(debit|credit)$"


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
