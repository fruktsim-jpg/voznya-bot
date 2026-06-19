"""Модели автономной активности друна: ивенты и предложения владельцу.

* :class:`DrunEvent` — структурированный автономный ивент (челлендж/прогноз/
  мини-ивент) с жизненным циклом и участниками. Phase 4.
* :class:`DrunProposal` — предложение высокоимпактного действия владельцу,
  ожидающее подтверждения. Phase 6.

Связи логические, без FK (соглашение проекта). Источник правды по деньгам —
экономическое ядро бота; здесь только описание/состояние.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DrunEvent(Base):
    """Один автономный ивент друна (челлендж/прогноз/мини-ивент)."""

    __tablename__ = "drun_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # challenge / prediction / mini_event / goal / ...
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # proposed → active → resolved → cancelled.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Кто создал (друн = NULL/0 либо owner_id, если предложил владелец).
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Награда (если есть): kind='eshki'|'item', amount/код в meta.
    reward_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reward_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Участники: [{id, joined_at, ...}] — кто записался/вовлечён.
    participants: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Исход (после resolve): победители, итог прогноза и т.п.
    outcome: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_drun_events_status", "status", "created_at"),
        Index("ix_drun_events_kind", "kind", "created_at"),
        Index("ix_drun_events_deadline", "deadline_at"),
    )


class DrunProposal(Base):
    """Предложение действия владельцу (approval-flow для высокоимпактных tool'ов)."""

    __tablename__ = "drun_proposals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # pending → approved → rejected → executed → expired.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Сериализованный tool-вызов (имя из registry + аргументы).
    tool: Mapped[str] = mapped_column(String(48), nullable=False)
    args: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    decided_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_drun_proposals_status", "status", "created_at"),)
