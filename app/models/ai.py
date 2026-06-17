"""Модели ИИ-нарратора «Тёмный друн».

Четыре таблицы, все правятся/наполняются без деплоя:

* ``AiSetting`` — key→JSONB конфиг провайдера (base_url/api_key/model/
  temperature/enabled). OpenAI-совместимый: OpenAI/OpenRouter/Claude/любой
  совместимый endpoint.
* ``AiPrompt`` — именованные промпты (persona/system/observation/...),
  редактируются из админки.
* ``AiMessage`` — краткосрочная память: история запросов/ответов.
* ``AiMemory`` — долгосрочная память: факты об игроках/мире.

Связи логические, без FK (соглашение проекта).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AiSetting(Base):
    """Одна настройка ИИ (ключ → JSONB-значение)."""

    __tablename__ = "ai_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | int | float | str | bool] = mapped_column(
        JSONB, nullable=False
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AiPrompt(Base):
    """Именованный промпт, редактируемый из админки без деплоя."""

    __tablename__ = "ai_prompts"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AiMessage(Base):
    """Краткосрочная память: одна реплика истории (запрос или ответ)."""

    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Канал диалога: chat / admin_test / dm_<id> и т.п.
    channel: Mapped[str] = mapped_column(
        String(32), nullable=False, default="chat"
    )
    # Роль: system / user / assistant.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # На кого/о ком (если применимо) и какое событие спровоцировало.
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trigger_event_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ai_messages_channel_created", "channel", "created_at"),
        Index("ix_ai_messages_user_created", "user_id", "created_at"),
    )


class AiMemory(Base):
    """Долгосрочная память: факт об игроке или мире."""

    __tablename__ = "ai_memories"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Про кого факт (users.id) или NULL = про мир в целом.
    subject_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Тип факта: fact / trait / rivalry / milestone.
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="fact"
    )
    fact: Mapped[str] = mapped_column(Text, nullable=False)
    # Важность для будущих упоминаний (выше → чаще всплывает в контексте).
    weight: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1
    )
    # Откуда факт: auto (из событий) / admin / drun.
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ai_memories_subject", "subject_id", "weight"),
        Index("ix_ai_memories_kind", "kind", "created_at"),
    )
