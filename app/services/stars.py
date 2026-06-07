"""Сервис Telegram Stars — единая точка записи движений в ``stars_ledger``.

Все приходы/расходы Stars обязаны проходить через этот сервис (как ешки — через
``economy.change_balance``). Это гарантирует единый журнал-источник правды,
дедупликацию входящих платежей по ``charge_id`` и пригодность к P&L/reconcile.

Сервис НЕ дергает Telegram API напрямую (баланс читается через
``telegram_gifts.get_star_balance`` вызывающим кодом и передаётся как
``balance_after``) — чтобы не смешивать внешний вызов и запись в БД.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.models import StarsLedger

logger = get_logger(__name__)


async def charge_exists(session: AsyncSession, charge_id: str) -> bool:
    """Уже зачисляли этот платёж? (идемпотентность входящих по charge_id)."""
    found = await session.scalar(
        select(StarsLedger.id).where(StarsLedger.charge_id == charge_id)
    )
    return found is not None


async def record_in(
    session: AsyncSession,
    *,
    amount_stars: int,
    reason: str,
    user_id: int | None,
    charge_id: str,
    source: str = "bot",
    balance_after: int | None = None,
    meta: dict | None = None,
) -> StarsLedger | None:
    """Фиксирует ПРИХОД Stars боту (топ-ап/донат). Идемпотентно по ``charge_id``.

    Возвращает созданную строку, либо ``None``, если платёж с таким ``charge_id``
    уже учтён (повторный апдейт от Telegram — не двоим).
    """
    if charge_id and await charge_exists(session, charge_id):
        logger.info("stars topup duplicate ignored: charge=%s", charge_id)
        return None

    row = StarsLedger(
        direction="in",
        amount_stars=amount_stars,
        reason=reason,
        user_id=user_id,
        charge_id=charge_id or None,
        source=source,
        balance_after=balance_after,
        meta=meta,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "stars IN: +%s reason=%s user=%s charge=%s",
        amount_stars, reason, user_id, charge_id,
    )
    return row


async def record_out(
    session: AsyncSession,
    *,
    amount_stars: int,
    reason: str,
    user_id: int | None,
    ref: str | None = None,
    source: str = "bot",
    balance_after: int | None = None,
    meta: dict | None = None,
) -> StarsLedger:
    """Фиксирует РАСХОД Stars ботом (например, отправка Gift через sendGift).

    ``sendGift`` не возвращает charge_id, поэтому расход не дедупится по charge_id —
    идемпотентность расхода обеспечивает вызывающий (статус доставки + ``ref`` =
    idempotency_key). Здесь просто пишем факт списания.
    """
    row = StarsLedger(
        direction="out",
        amount_stars=amount_stars,
        reason=reason,
        user_id=user_id,
        charge_id=None,
        ref=ref,
        source=source,
        balance_after=balance_after,
        meta=meta,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "stars OUT: -%s reason=%s user=%s ref=%s",
        amount_stars, reason, user_id, ref,
    )
    return row
