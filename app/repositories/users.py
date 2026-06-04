"""Запросы, связанные с пользователями."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import User


async def upsert_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    first_name: str | None,
    touch_activity: bool = True,
    increment_messages: bool = False,
) -> None:
    """Создаёт пользователя или обновляет его username/имя/активность.

    Используется в middleware на каждое сообщение. Реализовано через
    ``INSERT ... ON CONFLICT`` (атомарно и без гонок).

    :param increment_messages: если True — атомарно увеличивает messages_count
        на 1 (только для сообщений, не для нажатий кнопок).
    """
    values: dict = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
    }
    update_set: dict = {
        "username": username,
        "first_name": first_name,
    }
    if touch_activity:
        now = now_utc()
        values["last_active_at"] = now
        update_set["last_active_at"] = now
    if increment_messages:
        values["messages_count"] = 1
        update_set["messages_count"] = User.messages_count + 1

    stmt = pg_insert(User).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[User.user_id],
        set_=update_set,
    )
    await session.execute(stmt)


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    """Возвращает пользователя по ID."""
    return await session.get(User, user_id)


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Ищет пользователя по username (без учёта регистра, без ведущего @)."""
    username = username.lstrip("@").lower()
    if not username:
        return None
    result = await session.execute(
        select(User).where(func.lower(User.username) == username)
    )
    return result.scalars().first()


async def get_active_user_ids(
    session: AsyncSession, days: int, exclude: set[int] | None = None
) -> list[int]:
    """Возвращает ID пользователей, активных за последние ``days`` дней."""
    threshold = now_utc() - timedelta(days=days)
    stmt = select(User.user_id).where(User.last_active_at >= threshold)
    if exclude:
        stmt = stmt.where(User.user_id.notin_(exclude))
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def top_by_balance(session: AsyncSession, limit: int) -> list[User]:
    """Возвращает топ пользователей по балансу (по убыванию)."""
    result = await session.execute(
        select(User)
        .where(User.balance > 0)
        .order_by(User.balance.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def top_by_pidor(session: AsyncSession, limit: int) -> list[User]:
    """Возвращает топ пользователей по количеству статусов «Пидор дня»."""
    result = await session.execute(
        select(User)
        .where(User.pidor_count > 0)
        .order_by(User.pidor_count.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def rank_by_balance(session: AsyncSession, balance: int) -> int:
    """Возвращает место пользователя в рейтинге богатства (1 — самый богатый)."""
    higher = await session.scalar(
        select(func.count()).select_from(User).where(User.balance > balance)
    )
    return int(higher or 0) + 1
