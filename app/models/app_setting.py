"""Редактируемые из админки настройки (Admin V2, Этап 9).

Key-value поверх JSONB. БД ПЕРЕОПРЕДЕЛЯЕТ дефолты из ``app/settings/balance.py``;
если ключа нет — используется значение из кода. Читается ботом через
``app.settings.dynamic`` (кэш с TTL), правится сайт-админкой. Связи логические
(без FK) — конвенция проекта.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSetting(Base):
    """Одна редактируемая настройка (ключ → JSONB-значение)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict | list | int | float | str | bool] = mapped_column(
        JSONB, nullable=False
    )
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, default="general"
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
