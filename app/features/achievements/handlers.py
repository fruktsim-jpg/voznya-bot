"""Хендлеры команды /ачивки (достижения)."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.targets import resolve_target
from app.features.achievements.service import render_achievements
from app.repositories import users as users_repo
from app.services.deletion import get_deletion_service

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

    deletion = get_deletion_service()
    
    # Удаляем команду пользователя через 5 сек
    await deletion.schedule(session, message.chat.id, message.message_id, 5)
    
    # Отправляем ачивки и удаляем через 5 минут
    sent = await message.answer(await render_achievements(session, user_id))
    await deletion.schedule(session, sent.chat.id, sent.message_id, 300)
