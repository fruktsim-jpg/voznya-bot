"""Определение кейса — поведение предмета-кейса при открытии.

Кейс — это предмет каталога ``inventory_items`` с ``type='case'``. Эта таблица
описывает, как он открывается: стоимость (бесплатно / за ешки), списывает ли
сам предмет-кейс, активность и расписание (сезонные кейсы). Дроп-лист — в
``case_rewards``, история открытий — в ``case_openings``.

Связь с каталогом логическая по ``item_code`` (без FK — конвенция проекта).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Способ оплаты открытия. В V1 поддержаны free и currency; stars — задел.
CASE_COST_KINDS = ("free", "currency", "stars")


class CaseDefinition(Base):
    """Определение одного кейса (одна строка = один кейс)."""

    __tablename__ = "case_definitions"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    # → inventory_items.code (предмет-кейс, type='case'); один-к-одному.
    item_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Один из CASE_COST_KINDS.
    open_cost_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Стоимость открытия в ешках, если open_cost_kind='currency'.
    open_cost_amount: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    # Списывает ли открытие 1 предмет-кейс из инвентаря игрока.
    consumes_key: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # Задел под сезоны.
    season_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("item_code", name="uq_case_definitions_item"),
        CheckConstraint("open_cost_amount >= 0", name="ck_case_def_cost_nonneg"),
        CheckConstraint(
            "open_cost_kind IN ('free', 'currency', 'stars')",
            name="ck_case_def_cost_kind",
        ),
        Index("ix_case_definitions_active", "is_active"),
    )
