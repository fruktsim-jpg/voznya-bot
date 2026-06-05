"""Категории магазина — группировка офферов на витрине.

``shop_categories`` это чисто презентационная группировка (Титулы, Рамки,
Сезонное и т.п.). Сами товары — в ``shop_offers``, владение — в ``inventory``.
Категория не несёт экономики: ни цен, ни остатков.

Без внешних ключей (конвенция проекта): оффер ссылается на категорию по
строковому ``slug``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ShopCategory(Base):
    """Раздел витрины магазина."""

    __tablename__ = "shop_categories"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    # Стабильный код раздела, например "titles", "frames", "seasonal".
    slug: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Порядок сортировки на витрине (меньше — выше).
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Мягкое скрытие раздела без удаления.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
