from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class TransactionCreate(BaseModel):
    effective_date: date
    transaction_type: str = Field(pattern="^(payment|advance|fee|interest|adjustment)$")
    amount: float = Field(gt=0)
    note: str = ""
    source: str = "manual"
    reason: str = ""


class TransactionCorrection(BaseModel):
    effective_date: date
    transaction_type: str = Field(pattern="^(payment|advance|fee|interest|adjustment)$")
    amount: float = Field(gt=0)
    note: str = ""
    reason: str = Field(min_length=3)


class TransactionReverse(BaseModel):
    reason: str = Field(min_length=3)
