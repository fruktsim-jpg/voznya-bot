"""Per-instance владение предметами — задел под Telegram Gifts и серийники.

В отличие от стекового ``inventory`` (где одна строка = вид предмета + quantity),
здесь одна строка = ОДИН конкретный экземпляр. Нужно для предметов, у которых
есть собственная идентичность: Telegram-подарки (свой ``telegram_gift_id``,
статус выдачи), серийные лимитки («#3 из 100»), upgraded-подарки.

В Cases V1 таблица СОЗДАНА, но рантаймом НЕ используется (остаётся пустой) — это
страхует контракт схемы, чтобы будущие Gifts не требовали миграции владения.
Связи логические по ``item_code`` (без FK — конвенция проекта).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Состояние экземпляра. owned — обычное владение; pending/granted/failed —
# жизненный цикл асинхронной выдачи (Telegram Gifts); consumed — израсходован.
INSTANCE_STATES = ("owned", "pending", "granted", "failed", "consumed")


class InventoryInstance(Base):
    """Один конкретный экземпляр предмета у игрока."""

    __tablename__ = "inventory_instances"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Код предмета из каталога inventory_items.code.
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # Один из INSTANCE_STATES.
    instance_state: Mapped[str] = mapped_column(String(16), nullable=False)
    # Серийный номер для лимиток («#3 из series_total»).
    serial_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Идентификатор подарка в Telegram (если это Telegram Gift).
    telegram_gift_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_upgraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    collection_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Откуда получен: case / gift / admin / ...
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Связь с audit_log (без FK).
    audit_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_inventory_instances_user", "user_id"),
        Index("ix_inventory_instances_item", "item_code"),
        # Один телеграм-подарок не может принадлежать двум строкам.
        Index(
            "uq_inventory_instance_tg_gift",
            "telegram_gift_id",
            unique=True,
            postgresql_where="telegram_gift_id IS NOT NULL",
        ),
    )
