"""Модель пользователя — центральная сущность экономики и статистики."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    """Участник чата.

    Ключ — Telegram ``user_id`` (стабильный, в отличие от username).
    Денежные поля целочисленные (ешки неделимы).
    """

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Экономика
    balance: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, index=True
    )
    total_earned: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_spent: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Ферма / серия активности
    farm_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_farm_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_farm_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Игровая статистика
    treasures_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duels_won: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duels_lost: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pidor_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Счётчики для достижений
    farm_success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    casino_games_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Текущие серии проигрышей и крупнейший проигрыш в казино (секретки)
    casino_loss_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duel_loss_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_casino_loss: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Активность (для пула «активных за N дней»)
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def display_name(self) -> str:
        """Человекочитаемое имя для отображения."""
        if self.first_name:
            return self.first_name
        if self.username:
            return f"@{self.username}"
        return f"id{self.user_id}"
