"""Леджер открытий кейсов (append-only) — полная воспроизводимость по логам.

Каждое открытие пишет сюда строку: что выпало, какое число выпало (``roll``) и
слепок дроп-листа на момент открытия (``weight_snapshot``). Это «честный лог»:
даже если позже админ изменит веса, прошлое открытие остаётся проверяемым.

Отдельно от ``inventory_history`` (движение предметов) и ``transactions``
(движение ешек): здесь — «что и почему выпало», там — «движение активов».
Строки не редактируются и не удаляются. Связи логические по ``id`` (без FK).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CaseOpening(Base):
    """Одно открытие кейса (append-only)."""

    __tablename__ = "case_openings"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    case_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # → case_rewards.id (что выпало). NULL только для аварийных случаев.
    reward_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reward_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reward_item_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Выпавшее число в [0, SUM(weight)).
    roll: Mapped[int] = mapped_column(Integer, nullable=False)
    # Слепок дроп-листа на момент открытия (для воспроизводимости честности):
    # [{"reward_id": .., "weight": ..}, ...].
    weight_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Задел под provably-fair (seed). В V1 может быть NULL.
    server_seed: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Связи с другими леджерами (без FK).
    transaction_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    audit_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("roll >= 0", name="ck_case_openings_roll_nonneg"),
        CheckConstraint("qty >= 1", name="ck_case_openings_qty_pos"),
        Index("ix_case_openings_user", "user_id", "created_at"),
        Index("ix_case_openings_case", "case_item_code", "created_at"),
    )
