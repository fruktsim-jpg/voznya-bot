"""Выдача стекового предмета игроку + запись в леджер инвентаря.

Единственная функция записи во владение из рантайма бота (V1): выдать предмет
каталога в стековый ``inventory`` (upsert по (user_id, item_code) со сложением
quantity) и записать строку в append-only ``inventory_history``.

Не делает commit (его выполняет middleware), чтобы вызов мог участвовать в одной
транзакции с другими операциями (открытие кейса). Связи логические по
``item_code`` (без FK — конвенция проекта).

Сейчас работает только со стековыми предметами (``inventory_items.stackable``).
Per-instance предметы (Telegram Gifts, серийники) сюда НЕ попадают — для них
позже будет отдельный путь через ``inventory_instances`` (см. CASES_V1_PLAN).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Inventory, InventoryHistory, InventoryItem


class UnknownItem(Exception):
    """Предмета нет в каталоге inventory_items."""

    def __init__(self, item_code: str) -> None:
        self.item_code = item_code
        super().__init__(f"Unknown item_code: {item_code}")


async def grant_item(
    session: AsyncSession,
    *,
    user_id: int,
    item_code: str,
    quantity: int,
    source: str,
    event: str = "grant",
    actor_user_id: int | None = None,
    audit_id: int | None = None,
    transaction_id: int | None = None,
    meta: dict | None = None,
) -> None:
    """Выдаёт ``quantity`` предмета ``item_code`` игроку (стек) + запись в леджер.

    Предмет должен существовать в каталоге (иначе :class:`UnknownItem`). Слот
    копируется из каталога на момент выдачи (для частичного уникального индекса
    экипировки). Атомарный upsert: при гонке два начисления корректно сложатся.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    item = await session.scalar(
        select(InventoryItem).where(InventoryItem.code == item_code)
    )
    if item is None:
        raise UnknownItem(item_code)

    # Upsert владения: суммируем quantity при конфликте (user_id, item_code).
    stmt = (
        pg_insert(Inventory)
        .values(
            user_id=user_id,
            item_code=item_code,
            slot=item.slot,
            quantity=quantity,
            equipped=False,
            source=source,
        )
        .on_conflict_do_update(
            constraint="uq_inventory_user_item",
            set_={"quantity": Inventory.quantity + quantity},
        )
    )
    await session.execute(stmt)

    # Append-only леджер предметов.
    session.add(
        InventoryHistory(
            user_id=user_id,
            item_code=item_code,
            delta=quantity,
            event=event,
            source=source,
            actor_user_id=actor_user_id,
            audit_id=audit_id,
            transaction_id=transaction_id,
            meta=meta,
        )
    )


async def consume_item(
    session: AsyncSession,
    *,
    user_id: int,
    item_code: str,
    quantity: int = 1,
    source: str,
    event: str = "use",
    meta: dict | None = None,
) -> bool:
    """Списывает ``quantity`` предмета у игрока (под блокировкой строки).

    Возвращает True при успехе, False — если предмета не хватает. Пишет
    отрицательную дельту в ``inventory_history``. Строка владения блокируется
    ``FOR UPDATE`` — безопасно при гонках (двойной клик/два callback).
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    row = await session.scalar(
        select(Inventory)
        .where(Inventory.user_id == user_id)
        .where(Inventory.item_code == item_code)
        .with_for_update()
    )
    if row is None or row.quantity < quantity:
        return False

    remaining = row.quantity - quantity
    if remaining == 0:
        await session.delete(row)
    else:
        row.quantity = remaining

    session.add(
        InventoryHistory(
            user_id=user_id,
            item_code=item_code,
            delta=-quantity,
            event=event,
            source=source,
            meta=meta,
        )
    )
    return True
