"""Модель суточных номинаций: «Пидор дня» и «Пара дня».

Универсальная таблица: ``nomination_type`` различает виды номинаций,
что позволит в будущем добавлять новые («Красавчик дня» и т.п.).

Уникальный индекс (nomination_type, nomination_date) гарантирует, что
на каждый игровой день существует ровно одна запись — это решает гонку,
когда несколько пользователей одновременно «открывают» новый день.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DailyNomination(Base):
    """Результат суточной номинации за конкретный игровой день."""

    __tablename__ = "daily_nominations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Тип номинации: "pidor" / "para".
    nomination_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Игровая дата (с учётом сброса в 12:00).
    nomination_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Победитель (для «Пидора дня» — один пользователь).
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Второй участник (для «Пары дня»).
    user_id_2: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Кто первым «открыл» день этой номинацией.
    opened_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "nomination_type", "nomination_date", name="uq_nomination_type_date"
        ),
    )
