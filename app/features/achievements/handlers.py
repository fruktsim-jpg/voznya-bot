"""Хендлеры команды /ачивки (достижения)."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.targets import resolve_target
from app.features.achievements.service import render_achievements
from app.repositories import users as users_repo

router = Router(name="achievements")


@router.message(RuCommand("ачивки", "achievements"))
async def cmd_achievements(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Показывает достижения пользователя (свои или указанного)."""
    sender = message.from_user
    if sender is None:
        return

    target = await resolve_target(session, message, command_args)
    user = target or await users_repo.get_user(session, sender.id)
    user_id = user.user_id if user else sender.id

    await message.answer(await render_achievements(session, user_id))
