"""Модель открытых пользователем достижений."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserAchievement(Base):
    """Факт открытия достижения пользователем.

    Пара (user_id, code) уникальна — повторное открытие невозможно
    (гарантируется первичным ключом).
    """

    __tablename__ = "user_achievements"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
