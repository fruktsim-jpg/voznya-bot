"""Слой доступа к данным инвентаря игрока (read-only runtime V1).

Владение хранится в ``inventory`` (одна строка = вид предмета у игрока),
определение предмета — в каталоге ``inventory_items`` (связь по строковому
``item_code``, без FK — конвенция проекта). Здесь только ЧТЕНИЕ: показать
игроку его предметы и посчитать их. Запись (выдача/отзыв) идёт через админку
сайта (``/api/admin/inventory``) и в будущем — через магазин/кейсы/подарки.

Все функции принимают ``session: AsyncSession`` первым аргументом и не делают
commit — как в остальных репозиториях проекта.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Inventory, InventoryItem


@dataclass(frozen=True)
class InventoryRow:
    """Строка инвентаря игрока: владение + данные из каталога.

    ``name``/``rarity``/``type``/``description`` берутся из ``inventory_items``;
    если предмет в каталоге не найден (теоретически — рассинхрон), подставляются
    безопасные значения по ``item_code``.
    """

    item_code: str
    name: str
    rarity: str
    type: str
    description: str | None
    quantity: int
    equipped: bool


async def count_items(session: AsyncSession, user_id: int) -> int:
    """Возвращает СУММАРНОЕ число предметов игрока (с учётом quantity).

    Дешёвый агрегат для строки в профиле. Пустой инвентарь → 0.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(Inventory.quantity), 0)).where(
            Inventory.user_id == user_id
        )
    )
    return int(total or 0)


async def count_distinct_items(session: AsyncSession, user_id: int) -> int:
    """Возвращает число РАЗНЫХ предметов игрока (строк в inventory)."""
    total = await session.scalar(
        select(func.count())
        .select_from(Inventory)
        .where(Inventory.user_id == user_id)
    )
    return int(total or 0)


async def get_inventory(
    session: AsyncSession, user_id: int, *, limit: int | None = None, offset: int = 0
) -> list[InventoryRow]:
    """Возвращает инвентарь игрока с данными каталога (LEFT JOIN по коду).

    Сортировка стабильная: экипированные сверху, затем по редкости (через её
    числовой ранг из каталога невозможно одним SQL без CASE, поэтому сортируем
    по item_code как вторичному ключу — порядок редкости наводится при выводе).
    ``limit``/``offset`` — для пагинации; ``None`` отдаёт всё.
    """
    stmt = (
        select(
            Inventory.item_code,
            Inventory.quantity,
            Inventory.equipped,
            InventoryItem.name,
            InventoryItem.rarity,
            InventoryItem.type,
            InventoryItem.description,
        )
        .select_from(Inventory)
        .join(InventoryItem, InventoryItem.code == Inventory.item_code, isouter=True)
        .where(Inventory.user_id == user_id)
        .order_by(Inventory.equipped.desc(), Inventory.acquired_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)

    rows = (await session.execute(stmt)).all()
    return [
        InventoryRow(
            item_code=row[0],
            quantity=int(row[1] or 0),
            equipped=bool(row[2]),
            # Каталог мог не содержать запись (рассинхрон) — деградируем мягко.
            name=row[3] or row[0],
            rarity=row[4] or "common",
            type=row[5] or "cosmetic",
            description=row[6],
        )
        for row in rows
    ]
