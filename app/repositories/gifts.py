"""Слой доступа к данным магазина Gifts (каталог + доставки).

Только чтения и выборки под блокировкой для самой транзакции покупки/выдачи.
Запись (списание, резерв, журналы) — в сервисе :mod:`app.features.gifts.service`
(единая атомарная точка), как и в кейсах. Commit делает middleware.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GiftCatalog, GiftTransaction



async def get_active_gifts(session: AsyncSession) -> list[GiftCatalog]:
    """Активные позиции каталога, у которых есть остаток (или безлимит)."""
    stmt = (
        select(GiftCatalog)
        .where(GiftCatalog.is_active.is_(True))
        .order_by(GiftCatalog.sort_order, GiftCatalog.name)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    # Остаток = stock - reserved - sold_count (NULL stock = безлимит).
    return [
        g
        for g in rows
        if g.stock is None or (g.stock - g.reserved - g.sold_count) > 0
    ]


async def get_gift_by_code(
    session: AsyncSession, code: str
) -> GiftCatalog | None:
    """Позиция каталога по коду (без блокировки)."""
    return await session.scalar(
        select(GiftCatalog).where(GiftCatalog.code == code)
    )


async def get_gift_for_update(
    session: AsyncSession, code: str
) -> GiftCatalog | None:
    """Позиция каталога ПОД БЛОКИРОВКОЙ строки (для атомарной покупки)."""
    return await session.scalar(
        select(GiftCatalog).where(GiftCatalog.code == code).with_for_update()
    )


async def get_delivery_for_update(
    session: AsyncSession, idempotency_key: str
) -> GiftTransaction | None:
    """Запись доставки по idempotency_key ПОД БЛОКИРОВКОЙ (для выдачи/возврата)."""
    return await session.scalar(
        select(GiftTransaction)
        .where(GiftTransaction.idempotency_key == idempotency_key)
        .with_for_update()
    )


async def get_recent_deliveries(
    session: AsyncSession, *, user_id: int | None = None, limit: int = 50
) -> list[GiftTransaction]:
    """Последние доставки Telegram Gift (опц. по игроку), новые сверху."""
    stmt = (
        select(GiftTransaction)
        .where(GiftTransaction.kind == "tg_gift")
        .order_by(GiftTransaction.created_at.desc())
        .limit(limit)
    )
    if user_id is not None:
        stmt = stmt.where(GiftTransaction.recipient_user_id == user_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_pending_deliveries(
    session: AsyncSession, *, limit: int = 100
) -> list[GiftTransaction]:
    """ВСЕ оплаченные, но ещё не выданные доставки (status='pending').

    Берём напрямую по статусу (а не «последние N»), чтобы админ видел всю
    очередь на ручную выдачу, даже если pending накопилось много, а сверху
    висят свежие completed/cancelled. Старые сверху — выдаём по очереди.
    """
    stmt = (
        select(GiftTransaction)
        .where(GiftTransaction.kind == "tg_gift")
        .where(GiftTransaction.status == "pending")
        .order_by(GiftTransaction.created_at.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_pending_gifts_for_user(
    session: AsyncSession, user_id: int, *, limit: int = 50
) -> list[tuple[GiftTransaction, GiftCatalog | None]]:
    """Pending Telegram Gifts/Premium игрока + позиция каталога (для инвентаря).

    Это те же подарки, что показывает сайт в разделе «Инвентарь»: подарок живёт
    в ``gift_transactions`` (status='pending'), пока его не выдадут/продадут. Бот
    обязан показывать их в ``/инвентарь``, иначе инвентарь сайта и бота
    расходятся (единый источник правды — БД). Каталог нужен для ценности/продажи;
    при рассинхроне (позицию удалили) вернём None — рендер деградирует мягко.
    """
    stmt = (
        select(GiftTransaction, GiftCatalog)
        .join(GiftCatalog, GiftCatalog.code == GiftTransaction.item_code, isouter=True)
        .where(GiftTransaction.kind == "tg_gift")
        .where(GiftTransaction.recipient_user_id == user_id)
        .where(GiftTransaction.status == "pending")
        .order_by(GiftTransaction.created_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1]) for row in rows]


async def get_delivery_by_claim_token(
    session: AsyncSession, claim_token: str
) -> GiftTransaction | None:
    """Pending-доставка с данным ``meta.claim_token`` ПОД БЛОКИРОВКОЙ.

    Используется claim-flow «Подарить другу по ссылке»: получатель открывает
    ``/start gift_<token>``, мы находим ожидающую доставку по токену и выдаём её
    ему. Блокировка строки — защита от двойного клейма (две вкладки/гонка).
    """
    return await session.scalar(
        select(GiftTransaction)
        .where(GiftTransaction.kind == "tg_gift")
        .where(GiftTransaction.status == "pending")
        .where(GiftTransaction.meta.cast(JSONB).contains({"claim_token": claim_token}))
        .with_for_update()
    )


async def get_withdraw_requested(


    session: AsyncSession, *, limit: int = 50
) -> list[GiftTransaction]:
    """Pending-доставки, явно отправленные игроком на ВЫВОД (P2).

    Игрок нажал «Вывести» на сайте/в мини-аппе — действие пометило доставку
    ``meta.withdraw_requested = true`` (см. lib/inventory-actions.withdraw).
    Эти и только эти доставки авто-воркер пытается выдать. Подарки, которые
    игрок решил «оставить» в инвентаре, флага не имеют и воркером не трогаются.

    Фильтр по JSONB-флагу делаем в БД (``meta @> '{"withdraw_requested": true}'``),
    старые сверху — выдаём по очереди.
    """
    stmt = (
        select(GiftTransaction)
        .where(GiftTransaction.kind == "tg_gift")
        .where(GiftTransaction.status == "pending")
        .where(GiftTransaction.meta.cast(JSONB).contains({"withdraw_requested": True}))
        .order_by(GiftTransaction.created_at.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())



