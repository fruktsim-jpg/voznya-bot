"""Модель ожидающих подтверждения действий.

Используется для двухшаговых сценариев:
* дуэль: ``/бой`` → ``/го``;
* свадьба: ``/жениться`` → ``/да``;
* развод: ``/развод`` → ``/подтвердить``.

Действия имеют срок жизни (``expires_at``), после которого считаются
просроченными и не могут быть подтверждены.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Статусы
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_DECLINED = "declined"
STATUS_EXPIRED = "expired"

# Типы действий
TYPE_DUEL = "duel"
TYPE_MARRY = "marry"
TYPE_DIVORCE = "divorce"


class PendingAction(Base):
    """Действие, ожидающее подтверждения второй стороной."""

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)
    initiator_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # target_id может быть NULL для открытых вызовов на дуэль (любой может принять)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    # Сумма ставки (для дуэли); для остальных — NULL.
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_pending_target_status", "target_id", "status", "action_type"),
    )
