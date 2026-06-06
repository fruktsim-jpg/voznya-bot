"""Снимок пер-юзерной статистики из Combot (endpoint ``channel_users``).

Историческая выгрузка из Combot, импортируется ОДИН раз. Это «сырой» снимок
накопленных метрик игрока на момент импорта: сообщения, XP, репутация, дата
входа. Хранится отдельно и НЕ трогает ``users``/баланс/инвентарь — служит
источником для слоя достижений и для графиков.

Строки идемпотентны по ``user_id`` (повторный импорт = upsert). Связь с прогоном
импорта — логическая (``import_run_id``), без FK.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CombotUserStats(Base):
    """Накопленная статистика одного участника по данным Combot."""

    __tablename__ = "combot_user_stats"

    # Telegram user_id — канонический ключ снимка.
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # @username на момент выгрузки (может отсутствовать).
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Отображаемое имя (title) на момент выгрузки.
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Дата входа в чат (из поля joined, ms→tz). У старых может быть NULL.
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Дней в чате (поле dsj). NULL, если joined неизвестен.
    days_since_joined: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Всего сообщений за весь период наблюдения Combot.
    messages: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Опыт Combot (levels).
    xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Репутация Combot.
    rep: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Время последнего сообщения (last_message, ms→tz).
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Логическая ссылка на прогон импорта (combot_import_runs.id), без FK.
    import_run_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    # Полная сырая запись пользователя из ответа API (на всякий случай).
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Для топов активности.
        Index("ix_combot_user_messages", "messages"),
        # Для «старожилов» по дате входа.
        Index("ix_combot_user_joined", "joined_at"),
    )
