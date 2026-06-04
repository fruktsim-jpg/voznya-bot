"""Запросы для подсчёта сообщений (для сайта)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MessageDaily


async def increment_daily(session: AsyncSession, user_id: int, day: date) -> None:
    """Атомарно увеличивает счётчик сообщений пользователя за указанный день."""
    stmt = (
        pg_insert(MessageDaily)
        .values(user_id=user_id, day=day, count=1)
        .on_conflict_do_update(
            index_elements=[MessageDaily.user_id, MessageDaily.day],
            set_={"count": MessageDaily.count + 1},
        )
    )
    await session.execute(stmt)
