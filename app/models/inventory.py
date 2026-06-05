"""Владение предметами игроком + экипировка.

``inventory`` хранит, какие предметы есть у конкретного игрока и какие из них
экипированы. Определение предмета — в ``inventory_items`` (каталог), связь по
строковому ``item_code`` (без FK — конвенция проекта).

Экипировка: игрок может владеть многими предметами, но в каждом слоте
(title/frame/badge/avatar) активен максимум один. Это гарантируется частичным
уникальным индексом ``uq_inventory_one_equipped_per_slot`` на уровне БД —
экипировать второй титул физически нельзя, даже при гонке запросов.

Экономика и баланс (``users``) НЕ затрагиваются: инвентарь — отдельный слой.
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Inventory(Base):
    """Предмет, которым владеет игрок (одна строка = вид предмета у игрока)."""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Владелец (Telegram user_id; без FK, как transactions.user_id).
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Код предмета из каталога inventory_items.code.
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # Слот экипировки, скопированный из каталога на момент выдачи (NULL —
    # предмет не экипируется). Нужен для частичного уникального индекса.
    slot: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Количество (для стакающихся/коллекционных). Косметика обычно 1.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Экипирован ли предмет прямо сейчас.
    equipped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Откуда получен: shop / gift / admin / reward / event / migration.
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # Состояние конкретного экземпляра (серийный номер лимитки и т.п.).
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Один и тот же предмет не дублируется строками — стакаем quantity.
        UniqueConstraint("user_id", "item_code", name="uq_inventory_user_item"),
        # КЛЮЧЕВОЕ: максимум один экипированный предмет на слот у игрока.
        # Частичный уникальный индекс — действует только для equipped=true и
        # slot IS NOT NULL.
        Index(
            "uq_inventory_one_equipped_per_slot",
            "user_id",
            "slot",
            unique=True,
            postgresql_where=text("equipped = true AND slot IS NOT NULL"),
        ),
        # Быстрый просмотр инвентаря игрока.
        Index("ix_inventory_user", "user_id"),
        CheckConstraint("quantity >= 0", name="ck_inventory_quantity_nonneg"),
    )
