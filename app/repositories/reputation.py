"""Слой доступа к данным системы репутации.

Источник правды — журнал ``reputation_entries``. Текущая репутация игрока и
топы считаются агрегатами по этому журналу (``SUM(value)``), поэтому значение
всегда можно пересчитать из истории.

Все функции принимают ``session: AsyncSession`` первым аргументом и не делают
commit (его выполняет вызывающий код / middleware) — как в остальных
репозиториях проекта.

Репутация изолирована: не трогает users/balance/transactions/XP/messages/
shop/inventory/gift/Combot.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func, select

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import ReputationEntry, User


@dataclass(frozen=True)
class ReputationSummary:
    """Сводка репутации игрока."""

    score: int = 0  # Итог: плюсы минус минусы.
    plus: int = 0  # Количество +1.
    minus: int = 0  # Количество -1.


@dataclass(frozen=True)
class ReputationTopRow:
    """Строка топа репутации."""

    user_id: int
    first_name: str | None
    username: str | None
    score: int


async def get_summary(session: AsyncSession, user_id: int) -> ReputationSummary:
    """Возвращает сводку репутации игрока (итог, плюсы, минусы)."""
    plus_expr = func.coalesce(
        func.sum(case((ReputationEntry.value > 0, 1), else_=0)), 0
    )
    minus_expr = func.coalesce(
        func.sum(case((ReputationEntry.value < 0, 1), else_=0)), 0
    )
    row = (
        await session.execute(
            select(plus_expr, minus_expr).where(
                ReputationEntry.target_user_id == user_id
            )
        )
    ).first()
    if row is None:
        return ReputationSummary()
    plus = int(row[0] or 0)
    minus = int(row[1] or 0)
    return ReputationSummary(score=plus - minus, plus=plus, minus=minus)


async def seconds_until_available(
    session: AsyncSession,
    giver_user_id: int,
    target_user_id: int,
    cooldown_hours: int,
) -> float:
    """Сколько секунд осталось до права снова оценить этого игрока.

    Возвращает 0, если ограничение уже не действует (можно оценивать).
    Антиспам на пару «оценивающий → оценённый»: считается по времени
    последнего изменения именно этой пары.
    """
    last_at = await session.scalar(
        select(func.max(ReputationEntry.created_at)).where(
            ReputationEntry.giver_user_id == giver_user_id,
            ReputationEntry.target_user_id == target_user_id,
        )
    )
    if last_at is None:
        return 0.0
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=now_utc().tzinfo)
    elapsed = (now_utc() - last_at).total_seconds()
    remaining = cooldown_hours * 3600 - elapsed
    return remaining if remaining > 0 else 0.0


async def add_entry(
    session: AsyncSession,
    *,
    giver_user_id: int,
    target_user_id: int,
    value: int,
    reason: str | None,
) -> None:
    """Добавляет одно изменение репутации в журнал.

    Не делает commit — его выполнит вызывающий код. Антиспам и проверки
    (само-оценка, боты и т.п.) — ответственность сервиса/хендлера.
    """
    session.add(
        ReputationEntry(
            giver_user_id=giver_user_id,
            target_user_id=target_user_id,
            value=value,
            reason=reason,
        )
    )


async def top_by_reputation(
    session: AsyncSession, limit: int
) -> list[ReputationTopRow]:
    """Возвращает топ игроков по итоговой репутации (по убыванию)."""
    score_expr = func.coalesce(func.sum(ReputationEntry.value), 0).label("score")
    stmt = (
        select(
            ReputationEntry.target_user_id,
            User.first_name,
            User.username,
            score_expr,
        )
        .join(User, User.user_id == ReputationEntry.target_user_id)
        .group_by(ReputationEntry.target_user_id, User.first_name, User.username)
        .having(func.sum(ReputationEntry.value) != 0)
        .order_by(score_expr.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ReputationTopRow(
            user_id=row[0],
            first_name=row[1],
            username=row[2],
            score=int(row[3] or 0),
        )
        for row in rows
    ]


async def get_history(
    session: AsyncSession, user_id: int, limit: int = 20
) -> list[ReputationEntry]:
    """Возвращает последние изменения репутации игрока (новые сверху).

    Полезно для отладки/админки и пересчёта; в командах V1 не используется.
    """
    stmt = (
        select(ReputationEntry)
        .where(ReputationEntry.target_user_id == user_id)
        .order_by(ReputationEntry.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


