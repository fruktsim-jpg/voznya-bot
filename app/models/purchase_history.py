"""История покупок магазина (append-only).

``purchase_history`` фиксирует факт совершённой покупки: кто, что, за сколько,
когда и из какого источника. Это бизнес-запись магазина — отдельная от:

* ``transactions`` (финансовая проводка списания ешек) — связь по
  ``transaction_id``;
* ``inventory_history`` (движение предмета) — обе пишутся в одной транзакции;
* ``audit_log`` (если покупку инициировал админ).

Цена сохраняется снимком (``price``): оффер потом могут переоценить, а чек
покупки должен остаться неизменным.

Уникальность по лимиту на игрока обеспечивается частичным индексом
``uq_purchase_user_offer_unique`` для офферов с ограничением «1 на руки» — двойная
покупка такого предмета физически невозможна.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Источник покупки.
PURCHASE_SOURCES = ("shop", "admin", "event")


class PurchaseHistory(Base):
    """Одна совершённая покупка."""

    __tablename__ = "purchase_history"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Кто купил (Telegram user_id; без FK).
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Какой оффер (shop_offers.id) и какой предмет (снимок кода).
    offer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # За сколько (снимок цены на момент покупки, в ешках).
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Источник: shop / admin / event.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="shop"
    )
    # Связи с леджерами (без FK): финансовая проводка / админ-аудит.
    transaction_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    audit_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_purchase_history_user", "user_id", "created_at"),
        Index("ix_purchase_history_offer", "offer_id", "created_at"),
        # Для офферов «1 на руки» purchase-flow помечает строку meta.unique=true,
        # и этот частичный уникальный индекс не даёт второй такой покупки тем же
        # игроком того же оффера (защита от двойной покупки лимитки).
        Index(
            "uq_purchase_user_offer_unique",
            "user_id",
            "offer_id",
            unique=True,
            postgresql_where=text("(meta ->> 'unique') = 'true'"),
        ),
    )
