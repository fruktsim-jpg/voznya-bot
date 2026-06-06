"""Дневная история чата из Combot (endpoint ``channel_analytics``).

Одна строка = один день. Хранит дневные агрегаты: сообщения, активные
пользователи, вступления, выходы. Источник — тайм-серии ``messages`` /
``active_users`` / ``joined`` / ``left`` (массивы пар ``[ts_ms, value]``).

Append-only снимок истории. Не привязан к конкретным игрокам, нужен для
графиков и ретроспективы. День хранится как ``date`` (UTC), уникален.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CombotDailyStats(Base):
    """Агрегаты чата за один календарный день."""

    __tablename__ = "combot_daily_stats"

    # Календарный день (UTC) — естественный ключ ряда.
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    # Сообщений за день.
    messages: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Активных пользователей за день.
    active_users: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Вступлений за день.
    joins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Выходов за день.
    leaves: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Логическая ссылка на прогон импорта (без FK).
    import_run_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
