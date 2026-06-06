"""Каталог Telegram Gifts для магазина (этап 1: ассортимент + цены).

``gift_catalog`` — что игроки СМОГУТ купить за ешки (магазин Gifts из
``VOZNYA_ECONOMY_V2``). Отдельно от ``shop_offers``/``inventory_items``, потому
что Gift — это РЕАЛЬНЫЙ Telegram-подарок с себестоимостью в Stars (расход
владельца), а не косметический предмет инвентаря.

Этап 1 (этот код): только каталог и админ-управление — цена в ешках,
себестоимость в Stars (для P&L), запас/резерв. Поток покупки и автоматическая
отправка через Telegram API — следующий этап (поля ``stock``/``reserved`` уже
заложены под него).

Экономика (VOZNYA_ECONOMY_V2 §3–4): 1 Star ≈ 10 ешек; ``price_eshki`` обычно
≈ ``star_cost`` × 10 × наценка. Связи логические (без FK — конвенция проекта).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GiftCatalog(Base):
    """Позиция каталога подарков: Telegram Gift с ценой в ешках и запасом."""

    __tablename__ = "gift_catalog"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    # Стабильный машинный код, например 'gift_heart'.
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Себестоимость в Telegram Stars (расход владельца при отправке).
    star_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    # Цена для игрока в ешках (списывается через экономику при покупке).
    price_eshki: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Id подарка у Telegram (если известен; для будущей авто-выдачи).
    telegram_gift_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Запас/бюджет в штуках. NULL = безлимит (для реального расхода Stars не
    # рекомендуется — лучше явный пул). reserved — задел под резерв при покупке.
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )

    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_gift_catalog_active", "is_active", "sort_order"),
        CheckConstraint("star_cost >= 0", name="ck_gift_catalog_star_nonneg"),
        CheckConstraint("price_eshki >= 0", name="ck_gift_catalog_price_nonneg"),
        CheckConstraint("reserved >= 0", name="ck_gift_catalog_reserved_nonneg"),
        CheckConstraint("sold_count >= 0", name="ck_gift_catalog_sold_nonneg"),
        CheckConstraint(
            "stock IS NULL OR reserved <= stock",
            name="ck_gift_catalog_reserved_le_stock",
        ),
    )
