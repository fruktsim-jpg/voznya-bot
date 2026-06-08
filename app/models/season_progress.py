"""Сезонная прогрессия игрока: season MMR, титулы, login-streak, daily, missions.

Все таблицы привязаны к сезону логически через ``season_id`` (без FK —
конвенция проекта). Сбрасываются между сезонами вайпом сезонных данных, кроме
``login_streaks`` (стрик — это привычка игрока, не привязан к одному сезону, но
обнуляется при пропуске дня). См. docs/SEASON_1_WIPE_AND_DESIGN.md.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SeasonMmrEntry(Base):
    """Изменение СЕЗОННОГО MMR (журнал, источник правды текущего season MMR).

    Зеркалит ``mmr_entries``, но с привязкой к сезону. Lifetime MMR остаётся в
    ``mmr_entries``/``users.mmr``; season MMR копится с нуля каждый сезон.
    """

    __tablename__ = "season_mmr_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_season_mmr_season_player", "season_id", "player_id"),
    )


class SeasonTitleAward(Base):
    """Выданный сезонный титул (в финале сезона). Не активен в новом сезоне."""

    __tablename__ = "season_titles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("season_id", "player_id", "code", name="uq_season_title"),
        Index("ix_season_titles_player", "player_id"),
    )


class LoginStreak(Base):
    """Серия ежедневных заходов игрока (для daily reward и ачивок)."""

    __tablename__ = "login_streaks"

    player_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_login_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DailyClaim(Base):
    """Факт получения ежедневной награды в конкретный день (анти-двойной клейм)."""

    __tablename__ = "daily_claims"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    streak_day: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("player_id", "claim_date", name="uq_daily_claim"),
    )


class WeeklyMissionProgress(Base):
    """Прогресс недельного задания. week_start — понедельник недели (UTC)."""

    __tablename__ = "weekly_mission_progress"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    mission_code: Mapped[str] = mapped_column(String(32), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Когда задание выполнено и награда выдана (NULL = ещё не выдана).
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "player_id", "week_start", "mission_code", name="uq_weekly_mission"
        ),
        Index("ix_weekly_mission_player_week", "player_id", "week_start"),
    )
