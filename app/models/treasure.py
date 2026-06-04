"""Модель клада Возни."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Статусы клада
TREASURE_ACTIVE = "active"
TREASURE_CLAIMED = "claimed"
TREASURE_EXPIRED = "expired"


class Treasure(Base):
    """Клад, который появляется в чате 2–4 раза в сутки."""

    __tablename__ = "treasures"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reward: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=TREASURE_ACTIVE, nullable=False)
    # ID сообщения с кладом (чтобы при желании обновлять/удалять).
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    claimed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    spawned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_treasures_chat_status", "chat_id", "status"),
    )
