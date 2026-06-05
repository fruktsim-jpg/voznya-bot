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


@router.message(RuCommand("баланс", "balance", "бал", "деньги", "бабки"))
async def cmd_balance(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает баланс ешек пользователя."""
    user = message.from_user
    if user is None:
        return

    record = await users_repo.get_user(session, user.id)
    amount = record.balance if record else 0
    earned = record.total_earned if record else 0
    rank = await users_repo.get_user_rank_by_balance(session, user.id)
    
    deletion = get_deletion_service()
    
    # Формируем текст баланса
    balance_text = texts.BALANCE.format(
        mention=mention(user.id, user.first_name, user.username),
        balance=money(amount),
        title=get_title(earned).label,
    )
    
    # Добавляем место в топе
    if rank:
        balance_text += f"\n🏆 Место в топе: #{rank}"
    
    # Отправляем баланс
    sent = await message.answer(balance_text)
    
    # Автоудаление информационного сообщения
    await deletion.schedule_info_message(
        session,
        user_id=user.id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
    )
