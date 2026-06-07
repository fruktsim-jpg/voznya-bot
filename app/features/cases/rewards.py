"""Единая точка выдачи наград — ``grant_reward``.

ВСЕ награды кейсов (и в будущем — других механик) выдаются только здесь.
Альтернативных путей выдачи нет. Это точка расширения: добавление Telegram
Gifts / Stars = новая ветка в :func:`grant_reward`, без изменения кода кейсов,
профиля, инвентаря и сайта.

Поддержаны ветки:
* ``currency`` — начисление ешек через экономическое ядро (``change_balance`` +
  ``transactions``). Баланс напрямую НЕ трогается;
* ``item`` — выдача стекового предмета через ``inventory_grant`` (+ запись в
  ``inventory_history``);
* ``tg_gift`` — реальный Telegram Gift / Premium. НЕ выдаётся синхронно: его
  нельзя отправить внутри транзакции БД, а Premium вообще выдаётся только
  вручную. Вместо выдачи создаётся pending-``GiftTransaction`` — тот же
  жизненный цикл, что у магазина подарков (``pending → completed/cancelled``,
  ручная выдача командой ``/gifts_done``, идемпотентность по
  ``idempotency_key``). Дальше доставка идёт ОБЩИМ конвейером Gifts — без
  отдельной системы. ``reward_item_code`` указывает на код позиции
  ``gift_catalog`` (например ``gift_heart`` / ``gift_premium_3m``).

Ветка ``stars`` пока не реализована (поднимает ``NotImplementedError``).

Функция не делает commit — выполняется внутри транзакции вызывающего
(открытие кейса), чтобы выдача и леджер открытия были атомарны.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.economy_events import EVENT_REWARD
from app.models import GiftCatalog, GiftTransaction
from app.services.economy import change_balance
from app.services.inventory_grant import grant_item


@dataclass(frozen=True)
class RewardResult:
    """Итог выдачи награды (для рендера и записи в леджер открытия)."""

    reward_kind: str
    reward_item_code: str | None
    amount: int | None       # для currency — начисленные ешки
    qty: int                 # для item — выданное количество
    new_balance: int | None  # для currency — баланс после начисления
    # Для tg_gift — idempotency_key созданной pending-доставки (для уведомлений
    # игроку и ручной выдачи через тот же конвейер, что и магазин подарков).
    delivery_key: str | None = None


async def grant_reward(
    session: AsyncSession,
    *,
    user_id: int,
    reward_kind: str,
    reward_item_code: str | None,
    amount: int | None,
    qty: int,
    source: str = "case",
    transaction_meta: dict | None = None,
) -> RewardResult:
    """Выдаёт одну награду игроку. Диспетчер по ``reward_kind``.

    :raises NotImplementedError: для stars (пост-V1).
    :raises ValueError: при некорректных данных награды.
    """
    if reward_kind == "currency":
        if amount is None or amount <= 0:
            raise ValueError("currency reward requires positive amount")
        # Ешки идут ТОЛЬКО через экономическое ядро (леджер transactions).
        user = await change_balance(
            session,
            user_id,
            amount,
            reason=EVENT_REWARD,
            meta={"source": source, **(transaction_meta or {})},
        )
        return RewardResult(
            reward_kind="currency",
            reward_item_code=None,
            amount=amount,
            qty=1,
            new_balance=user.balance,
        )

    if reward_kind == "item":
        if not reward_item_code:
            raise ValueError("item reward requires reward_item_code")
        if qty <= 0:
            raise ValueError("item reward requires positive qty")
        # Предмет — через единый сервис выдачи (+ inventory_history).
        await grant_item(
            session,
            user_id=user_id,
            item_code=reward_item_code,
            quantity=qty,
            source=source,
            event="grant",
            meta=transaction_meta,
        )
        return RewardResult(
            reward_kind="item",
            reward_item_code=reward_item_code,
            amount=None,
            qty=qty,
            new_balance=None,
        )

    if reward_kind == "tg_gift":
        return await _grant_tg_gift(
            session,
            user_id=user_id,
            gift_code=reward_item_code,
            source=source,
            transaction_meta=transaction_meta,
        )

    if reward_kind == "stars":
        # Разрешено схемой (задел), не реализовано в рантайме.
        raise NotImplementedError("reward_kind 'stars' is not implemented yet")

    raise ValueError(f"unknown reward_kind: {reward_kind}")


async def _grant_tg_gift(
    session: AsyncSession,
    *,
    user_id: int,
    gift_code: str | None,
    source: str,
    transaction_meta: dict | None,
) -> RewardResult:
    """Создаёт pending-доставку реального Telegram Gift/Premium (НЕ выдаёт сразу).

    Награда-гифт не отправляется в момент открытия (Telegram-вызов нельзя делать
    внутри транзакции, а Premium выдаётся только вручную). Вместо этого пишем
    ``GiftTransaction(status='pending')`` — ту же запись, что создаёт покупка в
    магазине подарков. Дальше она попадает в очередь ``/gifts_pending`` и
    выдаётся тем же конвейером (``deliver_gift`` / ``complete_gift_manually``).

    Так все реальные призы (обычные гифты и Premium) идут одним путём, без
    отдельной системы выдачи.
    """
    if not gift_code:
        raise ValueError("tg_gift reward requires reward_item_code (gift code)")

    gift = await session.scalar(
        select(GiftCatalog).where(GiftCatalog.code == gift_code)
    )
    if gift is None:
        # Награда ссылается на несуществующую позицию каталога — это
        # мисконфигурация дроп-листа. Поднимаем → rollback всего открытия.
        raise ValueError(f"tg_gift reward references unknown gift '{gift_code}'")

    idem = f"casegift:{user_id}:{secrets.token_hex(8)}"
    meta = {
        "source": source,
        "gift": gift.code,
        "star_cost": gift.star_cost,
        "telegram_gift_id": gift.telegram_gift_id,
        **(transaction_meta or {}),
    }
    session.add(
        GiftTransaction(
            kind="tg_gift",
            gift_type="system",
            sender_user_id=None,
            recipient_user_id=user_id,
            item_code=gift.code,
            quantity=1,
            status="pending",
            idempotency_key=idem,
            meta=meta,
        )
    )
    return RewardResult(
        reward_kind="tg_gift",
        reward_item_code=gift.code,
        amount=None,
        qty=1,
        new_balance=None,
        delivery_key=idem,
    )
