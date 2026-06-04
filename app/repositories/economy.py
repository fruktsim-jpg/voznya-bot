"""Запросы, связанные с движением валюты (журнал транзакций)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import Transaction, User


async def weekly_top_earners(
    session: AsyncSession, days: int, limit: int
) -> list[tuple[User, int]]:
    """Возвращает топ пользователей по сумме заработка за последние ``days`` дней.

    Учитываются все положительные начисления: ферма, казино (выигрыши), дуэли,
    клады, достижения, бонусы номинаций и админ-начисления.
    """
    since = now_utc() - timedelta(days=days)
    earned = func.sum(Transaction.amount).label("earned")
    stmt = (
        select(User, earned)
        .join(Transaction, Transaction.user_id == User.user_id)
        .where(Transaction.amount > 0, Transaction.created_at >= since)
        .group_by(User.user_id)
        .order_by(earned.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row[0], int(row[1])) for row in result.all()]
