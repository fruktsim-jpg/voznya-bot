"""Запросы, связанные с браками."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Marriage


async def get_active_marriage(
    session: AsyncSession, user_id: int, lock: bool = False
) -> Marriage | None:
    """Возвращает активный брак пользователя (или None)."""
    stmt = (
        select(Marriage)
        .where(
            Marriage.divorced_at.is_(None),
            or_(Marriage.user_id_1 == user_id, Marriage.user_id_2 == user_id),
        )
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_married_user_ids(session: AsyncSession) -> set[int]:
    """Возвращает множество ID всех пользователей в активном браке."""
    result = await session.execute(
        select(Marriage.user_id_1, Marriage.user_id_2).where(
            Marriage.divorced_at.is_(None)
        )
    )
    ids: set[int] = set()
    for u1, u2 in result.all():
        ids.add(u1)
        ids.add(u2)
    return ids


async def top_longest_marriages(session: AsyncSession, limit: int) -> list[Marriage]:
    """Возвращает самые долгие активные браки (по дате свадьбы по возрастанию)."""
    result = await session.execute(
        select(Marriage)
        .where(Marriage.divorced_at.is_(None))
        .order_by(Marriage.married_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())
