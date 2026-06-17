"""``world_events`` — единый поток событий мира Возни.

Денормализованная проекция важных действий (дуэли, кейсы, ачивки, подарки,
сезоны, браки, крупные выигрыши) для ленты сайта и AI-нарратора (Тёмный друн).
Источник правды по деньгам/состоянию — прежние леджеры (``transactions`` и т.п.);
здесь — append-only поток «что произошло в мире», по которому удобно строить
ленту, хронику и реакции друна одним индексированным запросом.

Пишет ТОЛЬКО бот (Model 2). Связи логические, без FK (соглашение проекта).
``(ref_table, ref_id)`` уникальны при ненулевом ref_table — идемпотентность
бэкафилла и повторного emit.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorldEvent(Base):
    """Одно событие мира (для ленты и нарратора)."""

    __tablename__ = "world_events"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Тип события: case_open / case_jackpot / duel_won / gift_delivered / ...
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    # Главный участник (users.id) или NULL = событие мира/системы.
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Вторичный участник (проигравший дуэль, получатель подарка, супруг).
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Величина в ешках/Stars, где осмысленно.
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Исходный леджер и id строки (для drill-down и идемпотентности).
    ref_table: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Вес для тиринга ленты и порогов друна: 0 болтовня .. 3 легендарное.
    severity: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0
    )
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_world_events_created_at", "created_at"),
        Index("ix_world_events_type_created", "type", "created_at"),
        Index("ix_world_events_actor_created", "actor_id", "created_at"),
        Index("ix_world_events_severity_created", "severity", "created_at"),
    )
