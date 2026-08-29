from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Person(Base):
    __tablename__ = "people"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accounts: Mapped[list[Account]] = relationship(back_populates="person")


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    name: Mapped[str] = mapped_column(String(180))
    account_type: Mapped[str] = mapped_column(String(40), default="loan")
    opening_principal: Mapped[float] = mapped_column(Float, default=0.0)
    annual_interest_rate: Mapped[float] = mapped_column(Float, default=0.0)
    regular_payment: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    legacy_id: Mapped[str | None] = mapped_column(String(180), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    person: Mapped[Person] = relationship(back_populates="accounts")
    transactions: Mapped[list[LedgerTransaction]] = relationship(
        back_populates="account",
        foreign_keys="LedgerTransaction.account_id",
        order_by=lambda: (LedgerTransaction.effective_date, LedgerTransaction.id),
    )


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    transaction_type: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(10), default="debit")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    note: Mapped[str] = mapped_column(Text, default="")
    reference: Mapped[str] = mapped_column(String(160), default="")
    source: Mapped[str] = mapped_column(String(40), default="manual")
    created_by: Mapped[str] = mapped_column(String(120), default="local")
    legacy_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reverses_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_transactions.id"), nullable=True, index=True)
    correction_group: Mapped[str] = mapped_column(String(64), default="", index=True)
    previous_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    account: Mapped[Account] = relationship(back_populates="transactions", foreign_keys=[account_id])
    reversed_transaction: Mapped[LedgerTransaction | None] = relationship(remote_side=[id], foreign_keys=[reverses_transaction_id], uselist=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    summary: Mapped[str] = mapped_column(Text)
    before_json: Mapped[str] = mapped_column(Text, default="")
    after_json: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(120), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ApplicationSetting(Base):
    __tablename__ = "application_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
