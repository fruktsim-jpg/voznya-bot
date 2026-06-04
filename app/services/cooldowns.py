"""Сервис кулдаунов — общий для всех команд с ограничением частоты."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import Cooldown


async def get_remaining(session: AsyncSession, user_id: int, action: str) -> float:
    """Возвращает, сколько секунд осталось до конца кулдауна.

    0.0 — кулдаун окончён или не устанавливался.
    """
    cooldown = await session.get(Cooldown, (user_id, action))
    if cooldown is None:
        return 0.0
    remaining = (cooldown.available_at - now_utc()).total_seconds()
    return remaining if remaining > 0 else 0.0


async def set_cooldown(
    session: AsyncSession, user_id: int, action: str, seconds: int
) -> None:
    """Устанавливает (или продлевает) кулдаун действия для пользователя."""
    available_at = now_utc() + timedelta(seconds=seconds)
    cooldown = await session.get(Cooldown, (user_id, action))
    if cooldown is None:
        session.add(
            Cooldown(user_id=user_id, action=action, available_at=available_at)
        )
    else:
        cooldown.available_at = available_at


async def clear_cooldown(session: AsyncSession, user_id: int, action: str) -> None:
    """Снимает кулдаун (например, если действие не состоялось)."""
    cooldown = await session.get(Cooldown, (user_id, action))
    if cooldown is not None:
        await session.delete(cooldown)
