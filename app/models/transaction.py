"""Модель транзакции — журнал (леджер) всех движений ешек.

Каждое изменение баланса фиксируется отдельной строкой. Это даёт:
* честную историю «всего заработано/потрачено»;
* возможность разбирать спорные ситуации;
* фундамент для будущей экономики (банк, магазин, лотереи).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Transaction(Base):
    """Одна запись о движении валюты."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    # Положительное значение — начисление, отрицательное — списание.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Причина: farm / casino / duel / treasure / marriage / admin / ...
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    # Произвольные детали операции (множитель казино, исход дуэли и т.п.).
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_transactions_user_reason", "user_id", "reason"),
        # Под дневной лимит эконом-выходок друна (_ops_today): выборка по
        # reason с временной границей, без user_id (см. 0046_drun_econ_index).
        Index("ix_transactions_reason_created", "reason", "created_at"),
    )
