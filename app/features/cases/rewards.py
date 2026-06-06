"""Единая точка выдачи наград — ``grant_reward``.

ВСЕ награды кейсов (и в будущем — других механик) выдаются только здесь.
Альтернативных путей выдачи нет. Это точка расширения: добавление Telegram
Gifts / Stars = новая ветка в :func:`grant_reward`, без изменения кода кейсов,
профиля, инвентаря и сайта.

В V1 поддержаны две ветки:
* ``currency`` — начисление ешек через экономическое ядро (``change_balance`` +
  ``transactions``). Баланс напрямую НЕ трогается;
* ``item`` — выдача стекового предмета через ``inventory_grant`` (+ запись в
  ``inventory_history``).

Ветки ``tg_gift`` и ``stars`` намеренно не реализованы (поднимают
``NotImplementedError``) — это задел, разрешённый схемой, но запрещённый
рантаймом V1.

Функция не делает commit — выполняется внутри транзакции вызывающего
(открытие кейса), чтобы выдача и леджер открытия были атомарны.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.economy_events import EVENT_REWARD
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

    :raises NotImplementedError: для tg_gift/stars (пост-V1).
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

    if reward_kind in ("tg_gift", "stars"):
        # Разрешено схемой (задел), запрещено рантаймом V1. Включение = реализация
        # этой ветки (через inventory_instances / telegram_payments).
        raise NotImplementedError(
            f"reward_kind '{reward_kind}' is post-V1 (Telegram Gifts/Stars)"
        )

    raise ValueError(f"unknown reward_kind: {reward_kind}")
