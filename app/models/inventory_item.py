"""Каталог предметов — универсальное определение того, что вообще существует.

``inventory_items`` это СПРАВОЧНИК (что за предмет), а не владение. Владение
игроком хранится в ``inventory`` (таблица :mod:`app.models.inventory`).

Один предмет каталога описывает титул, рамку, бейдж, аватар, цвет профиля,
коллекционную или событийную вещь, а в будущем — игровые предметы. Тип и слот
экипировки задают поведение; редкость — визуал и ценность; ``payload`` хранит
специфику (hex-цвет, ссылку на картинку и т.п.) без новых колонок.

Намеренно без внешних ключей (конвенция проекта): связь с ``inventory`` идёт по
строковому ``code``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
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

# Тип предмета — определяет, к какой механике он относится.
ITEM_TYPES = (
    "cosmetic",     # общий косметический
    "title",        # титул (текст рядом с именем)
    "badge",        # бейдж/значок
    "frame",        # рамка аватара
    "avatar",       # аватар/картинка профиля
    "collectible",  # коллекционный (не экипируется, ценность в обладании)
    "event",        # событийный/временный
)

# Редкость — визуал и ценность.
ITEM_RARITIES = ("common", "uncommon", "rare", "epic", "legendary")

# Слоты экипировки. Предмет можно экипировать, только если у него есть слот;
# в каждом слоте у игрока активен максимум один предмет (см. inventory).
EQUIP_SLOTS = ("title", "frame", "badge", "avatar")


class InventoryItem(Base):
    """Определение предмета в каталоге (одна строка = один вид предмета)."""

    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    # Стабильный машинный код, например "title_legend", "frame_gold".
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Один из ITEM_TYPES.
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Слот экипировки (один из EQUIP_SLOTS) или NULL — предмет не экипируется
    # (collectible/event/часть cosmetic). Денормализуется в inventory при выдаче
    # ради ограничения «один активный предмет на слот».
    slot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Один из ITEM_RARITIES.
    rarity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="common"
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Специфика предмета без новых колонок: {"color": "#ff0", "image": "...",
    # "text": "Легенда"} и т.п.
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Лимитированность: is_limited + max_supply (NULL = безлимит). Сколько уже
    # выдано — считается по inventory/inventory_history, здесь не дублируем.
    is_limited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    max_supply: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Доступен ли предмет к выдаче/витрине (мягкое отключение без удаления).
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # Можно ли дарить/передавать предмет между игроками. Непередаваемые
    # (привязанные) предметы дарить нельзя — см. GIFT_FOUNDATION.md.
    transferable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


    __table_args__ = (
        # Выборка активной витрины/каталога по типу и редкости.
        Index("ix_inventory_items_type_active", "type", "is_active"),
    )
