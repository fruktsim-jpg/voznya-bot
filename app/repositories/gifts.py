"""Слой доступа к данным магазина Gifts (каталог + доставки).

Только чтения и выборки под блокировкой для самой транзакции покупки/выдачи.
Запись (списание, резерв, журналы) — в сервисе :mod:`app.features.gifts.service`
(единая атомарная точка), как и в кейсах. Commit делает middleware.
"""

from __future__ import annotations

from sqlalchemy import select
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


