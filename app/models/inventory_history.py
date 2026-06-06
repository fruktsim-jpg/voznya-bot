"""История движений предметов (append-only леджер инвентаря).

Каждое получение и удаление предмета пишет сюда строку — это «леджер предметов»,
аналог ``transactions`` для валюты. Даёт честную историю «откуда взялось / куда
делось», нужен для разбора споров и для раздела истории в Mini App.

Строки не редактируются и не удаляются. Денежные покупки дополнительно
отражаются в ``transactions``; админ-выдача — ещё и в ``audit_log``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Причина движения предмета.
INVENTORY_SOURCES = ("shop", "gift", "admin", "reward", "event", "migration", "case")


# Тип события движения.
INVENTORY_EVENTS = ("grant", "revoke", "purchase", "use", "equip", "unequip")


class InventoryHistory(Base):
    """Одна запись о движении предмета."""

    __tablename__ = "inventory_history"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Игрок-владелец.
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Код предмета (снимок: запись остаётся валидной, даже если предмет уберут).
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # Изменение количества: + получено / − снято. Для equip/unequip = 0.
    delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Тип события (один из INVENTORY_EVENTS).
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    # Причина/источник (один из INVENTORY_SOURCES).
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # Кто инициировал (user_id админа/дарителя) — NULL для системных/наградных.
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Связи с другими леджерами (без FK): id строки audit_log / transactions.
    audit_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transaction_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    # Произвольные детали (например {"to_user_id": ...} для подарка).
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_inventory_history_user", "user_id", "created_at"),
        Index("ix_inventory_history_item", "item_code", "created_at"),
    )
