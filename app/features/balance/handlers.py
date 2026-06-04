"""Хендлеры команды /баланс."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.money import money
from app.core.utils import mention
from app.repositories import users as users_repo
from app.services.deletion import get_deletion_service
from app.settings import texts
from app.settings.titles import get_title

router = Router(name="balance")


@router.message(RuCommand("баланс", "balance"))
async def cmd_balance(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает баланс ешек пользователя."""
    user = message.from_user
    if user is None:
        return

    record = await users_repo.get_user(session, user.id)
    amount = record.balance if record else 0
    earned = record.total_earned if record else 0
    
    deletion = get_deletion_service()
    
    # Удаляем команду пользователя через 5 сек
    await deletion.schedule(session, message.chat.id, message.message_id, 5)
    
    # Отправляем баланс и удаляем через 2 минуты
    sent = await message.answer(
        texts.BALANCE.format(
            mention=mention(user.id, user.first_name, user.username),
            balance=money(amount),
            title=get_title(earned).label,
        )
    )
    await deletion.schedule(session, sent.chat.id, sent.message_id, 120)
