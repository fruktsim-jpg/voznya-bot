"""Состояние модерации игрока — текущие ограничения.

Одна строка на игрока (``user_id`` это PK). NULL в ``banned_until`` /
``muted_until`` означает отсутствие соответствующего ограничения. Время —
в UTC (как и везде в БД). Источник истины — бот: он реально применяет
бан/мьют через Telegram и читает эту таблицу в middleware. Сайт пишет сюда
аудируемые изменения.

Внешних ключей намеренно нет (соглашение проекта, см. admin_role.py).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserModeration(Base):
    """Текущие ограничения игрока (бан/мьют/счётчик варнов)."""

    __tablename__ = "user_moderation"

    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    banned_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    muted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    warn_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    ban_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    mute_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    tg_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
