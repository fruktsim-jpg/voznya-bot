"""Профиль игрока глазами Тёмного друна (богатое досье, живёт между ответами).

Дешёвая память (``ai_memories``) — это разрозненные факты. Профиль — это
СОБРАННЫЙ портрет одного человека: кто он, как говорит, с кем дружит/враждует,
его болевые точки и победы. Друн строит его из ВСЕЙ доступной базы (баланс, mmr,
репутация, дуэли, брак, ачивки, кейсы, подарки, ферма, история сообщений) плюс
LLM-саммари личности и манеры речи.

Обновляется в реальном времени (вскоре после активности игрока) и периодическим
свипом. Связи логические, без FK (соглашение проекта).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AiProfile(Base):
    """Собранный портрет одного игрока для контекста друна."""

    __tablename__ = "ai_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Краткое саммари личности (1-3 фразы, живым языком) — кто это вообще.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Манера речи: как человек пишет (мат, капс, смайлы, длина, словечки).
    speech_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Структурные данные: traits[], topics[], quirks[], stats{}, relationships[].
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Снимок статы на момент сборки (для дешёвого показа без джойнов).
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Сколько реплик игрока учтено — чтобы не пересобирать зря.
    messages_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Когда профиль перестраивался последний раз (для дебаунса реалтайма).
    refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ai_profiles_refreshed", "refreshed_at"),
    )
