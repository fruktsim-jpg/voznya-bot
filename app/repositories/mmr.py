"""Слой доступа к данным системы рейтинга MMR.

Источник правды — журнал ``mmr_entries``. Текущий MMR игрока и топы считаются
агрегатами по этому журналу (``SUM(amount)``), поэтому значение всегда можно
пересчитать из истории.

Все функции принимают ``session: AsyncSession`` первым аргументом и не делают
commit (его выполняет вызывающий код / middleware) — как в остальных
репозиториях проекта.

MMR изолирован: не трогает users/balance/transactions/репутацию/messages/
shop/inventory/gift/Combot.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MmrEntry, User


@dataclass(frozen=True)
class MmrTopRow:
    """Строка топа рейтинга."""

    user_id: int
    first_name: str | None
    username: str | None
    mmr: int


async def get_mmr(session: AsyncSession, user_id: int) -> int:
    """Возвращает текущий рейтинг игрока (``SUM(amount)``)."""
    total = await session.scalar(
        select(func.coalesce(func.sum(MmrEntry.amount), 0)).where(
            MmrEntry.player_id == user_id
        )
    )
    return int(total or 0)


async def add_entry(
    session: AsyncSession,
    *,
    player_id: int,
    amount: int,
    source: str,
    reason: str | None,
) -> None:
    """Добавляет одно изменение рейтинга в журнал.

    Не делает commit — его выполнит вызывающий код. Каждое изменение MMR
    логируется отдельной строкой (история — источник правды).
    """
    session.add(
        MmrEntry(
            player_id=player_id,
            amount=amount,
            source=source,
            reason=reason,
        )
    )


async def top_by_mmr(session: AsyncSession, limit: int) -> list[MmrTopRow]:
    """Возвращает топ игроков по рейтингу (по убыванию)."""
    mmr_expr = func.coalesce(func.sum(MmrEntry.amount), 0).label("mmr")
    stmt = (
        select(
            MmrEntry.player_id,
            User.first_name,
            User.username,
            mmr_expr,
        )
        .join(User, User.user_id == MmrEntry.player_id)
        .group_by(MmrEntry.player_id, User.first_name, User.username)
        .having(func.sum(MmrEntry.amount) > 0)
        .order_by(mmr_expr.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        MmrTopRow(
            user_id=row[0],
            first_name=row[1],
            username=row[2],
            mmr=int(row[3] or 0),
        )
        for row in rows
    ]


async def get_history(
    session: AsyncSession, user_id: int, limit: int = 20
) -> list[MmrEntry]:
    """Возвращает последние изменения рейтинга игрока (новые сверху).

    Полезно для отладки/админки и пересчёта; в командах V1 не используется.
    """
    stmt = (
        select(MmrEntry)
        .where(MmrEntry.player_id == user_id)
        .order_by(MmrEntry.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
