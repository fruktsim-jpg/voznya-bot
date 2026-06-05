"""Оффер магазина — то, что выставлено на продажу.

``shop_offers`` = «предмет каталога + цена + условия продажи». Сам предмет
(титул/рамка/бейдж/аватар/коллекционка) определён в ``inventory_items``; оффер
ссылается на него по ``item_code`` (без FK). Это разделяет «что существует» от
«что и почём продаётся»: один предмет можно продавать в разные периоды по разной
цене разными офферами.

Лимитированность живёт здесь (на оффере), а не в каталоге: лимит — это свойство
конкретной продажи. ``max_supply`` + ``sold_count`` дают остаток; защита от гонок
описана в ``SHOP_FOUNDATION.md`` (атомарный UPDATE с условием остатка).

Деньги не дублируются: цена в ешках — это сумма списания через существующую
экономику; баланс остаётся в ``users``, проводка — в ``transactions``.
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ShopOffer(Base):
    """Товар на витрине: предмет каталога с ценой и условиями продажи."""

    __tablename__ = "shop_offers"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    # Предмет из каталога: inventory_items.code (без FK).
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # Раздел витрины: shop_categories.slug (без FK), NULL — вне разделов.
    category_slug: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )

    # Цена в ешках (целые, ешки неделимы). Списывается через transactions.
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # --- Лимитированность (свойство оффера, не каталога) ---
    is_limited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Тираж оффера. NULL = безлимит. Имеет смысл только при is_limited=true.
    max_supply: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Сколько уже продано. Инкрементируется атомарно при покупке (см. flow).
    sold_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Лимит на одного игрока (например 1 экземпляр). NULL = без ограничения.
    per_user_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Сезонность / окно продаж ---
    # Сезонный оффер виден/продаётся только в окне [starts_at, ends_at].
    is_seasonal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Продаётся ли сейчас (ручное вкл/выкл админом). Финальная доступность =
    # is_active И (не лимит ИЛИ остаток>0) И (не сезон ИЛИ сейчас в окне).
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

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
        # Выборка активной витрины по разделу.
        Index("ix_shop_offers_category_active", "category_slug", "is_active"),
        Index("ix_shop_offers_item", "item_code"),
        CheckConstraint("price >= 0", name="ck_shop_offers_price_nonneg"),
        CheckConstraint("sold_count >= 0", name="ck_shop_offers_sold_nonneg"),
        # Остаток не уходит в минус: sold_count не превышает тираж (когда задан).
        CheckConstraint(
            "max_supply IS NULL OR sold_count <= max_supply",
            name="ck_shop_offers_sold_le_supply",
        ),
    )
