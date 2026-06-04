"""Модель ежедневного счётчика сообщений (для сайта).

На каждое сообщение пользователя счётчик за текущий день (в часовом поясе
Europe/Amsterdam) увеличивается на 1. Используется только для статистики/сайта
и не влияет на игровую логику.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MessageDaily(Base):
    """Количество сообщений пользователя за конкретный день."""

    __tablename__ = "message_daily"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    __table_args__ = (
        Index("ix_message_daily_day", "day"),
    )
