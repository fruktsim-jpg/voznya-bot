"""Модели ИИ-нарратора «Тёмный друн».

Четыре таблицы, все правятся/наполняются без деплоя:

* ``AiSetting`` — key→JSONB конфиг провайдера (base_url/api_key/model/
  temperature/enabled). OpenAI-совместимый: OpenAI/OpenRouter/Claude/любой
  совместимый endpoint.
* ``AiPrompt`` — именованные промпты (persona/system/observation/...),
  редактируются из админки.
* ``AiMessage`` — краткосрочная память: история запросов/ответов.
* ``AiMemory`` — долгосрочная память: факты об игроках/мире.
* ``AiChatArchive`` — сырой архив исторических реплик для retrieval.
* ``AiJobHealth`` — состояние фоновых джобов Друна.

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
from sqlalchemy.types import UserDefinedType

from app.models.base import Base


class _Vector(UserDefinedType):
    """Минимальный SQLAlchemy-тип для pgvector ``vector(dim)``.

    Полноценный ``pgvector.sqlalchemy.Vector`` тянет лишний пакет; нам нужно
    только описание колонки для DDL/рефлексии — все векторные операции
    (``<=>``, ``vector_cosine_ops``) уже выполняются в сыром SQL из
    ``drun/memory.py``. Чтение/запись через ORM нам не нужно (embedder пишет
    параметризованным UPDATE), поэтому bind/result процессоры оставляем
    стандартные — SQLAlchemy кастует к строке вида ``[0.1,0.2,...]``,
    которую pgvector принимает напрямую.
    """

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_: object) -> str:  # noqa: D401
        return f"vector({self.dim})"


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
    # onupdate: при ЛЮБОМ ORM-апдейте строки (например, переподтверждение
    # урока в reflect.py поднимает weight) колонка обновляется. Без этого
    # ранжирование/пруунинг уроков по updated_at работали по константе
    # (created_at), и свежеподтверждённые уроки выглядели «старыми».
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Семантический эмбеддинг факта. NULL = ещё не посчитан (бэкафилл-джоб
    # добьёт), либо embedder выключен. Размерность 384 под локальный fastembed.
    # Тип хранится как pgvector ``vector(384)``; в Python с этой колонкой
    # напрямую не работаем (запись/поиск идут сырым SQL в drun/memory.py и
    # drun/embeddings.py), поэтому Mapped[None]-аннотация и Optional[Any].
    embedding: Mapped[object | None] = mapped_column(
        _Vector(384), nullable=True
    )

    __table_args__ = (
        Index("ix_ai_memories_subject", "subject_id", "weight"),
        Index("ix_ai_memories_kind", "kind", "created_at"),
    )


class AiChatArchive(Base):
    """Сырой исторический чат: реальные реплики из export для поиска/цитат."""

    __tablename__ = "ai_chat_archive"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    embedding: Mapped[object | None] = mapped_column(
        _Vector(384), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_ai_chat_archive_source_msg", "source", "source_message_id", unique=True),
        Index("ix_ai_chat_archive_user_time", "user_id", "message_at"),
        Index("ix_ai_chat_archive_source_time", "source", "message_at"),
    )


class AiPersonMention(Base):
    """Normalized person/name mentions mined from raw chat archive."""

    __tablename__ = "ai_person_mentions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    archive_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mention: Mapped[str] = mapped_column(String(96), nullable=False)
    mention_norm: Mapped[str] = mapped_column(String(96), nullable=False)
    speaker_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    speaker_name: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    text_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="regex")
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_ai_person_mentions_archive_norm", "archive_id", "mention_norm", unique=True),
        Index("ix_ai_person_mentions_norm_time", "mention_norm", "message_at"),
        Index("ix_ai_person_mentions_speaker", "speaker_user_id", "message_at"),
    )


class AiJobHealth(Base):
    """Last-run status for Drun/background jobs."""

    __tablename__ = "ai_job_health"

    job_name: Mapped[str] = mapped_column(String(96), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    runs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    successes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ai_job_health_updated", "updated_at"),
    )
