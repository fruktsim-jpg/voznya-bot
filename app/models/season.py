"""Сезон — окно прогрессии Возни (Сезон 1+).

``seasons`` хранит сами сезоны. Активен максимум один (``is_active=True``).
Сезонные сущности (MMR, титулы, ачивки, задания) ссылаются на ``season.id``
логически (без FK — конвенция проекта). См. docs/SEASON_1_WIPE_AND_DESIGN.md.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Season(Base):
    """Один игровой сезон."""

    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Активен ровно один сезон. Финализированный сезон → is_active=False.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
