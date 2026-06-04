"""Модель кулдаунов — универсальная для любых команд.

Хранит, когда действие снова станет доступно для пользователя.
Новые механики получают поддержку кулдаунов «бесплатно».
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Cooldown(Base):
    """Кулдаун конкретного действия для конкретного пользователя."""

    __tablename__ = "cooldowns"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    action: Mapped[str] = mapped_column(String(32), primary_key=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
