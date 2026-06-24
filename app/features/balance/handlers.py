"""Хендлеры команды /баланс."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.money import money
from app.core.responses import send_info_window
from app.core.utils import mention
from app.repositories import users as users_repo
from app.settings import texts
from app.settings.titles import get_title

router = Router(name="balance")


@router.message(RuCommand("баланс", "balance", "бал", "деньги", "мои деньги", "кошелёк", "кошелек", "бабки"))

async def cmd_balance(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает баланс ешек пользователя."""
    user = message.from_user
    if user is None:
        return

    record = await users_repo.get_user(session, user.id)
    amount = record.balance if record else 0
    earned = record.total_earned if record else 0
    rank = await users_repo.get_user_rank_by_balance(session, user.id)
    
    # Формируем текст баланса
    balance_text = texts.BALANCE.format(
        mention=mention(user.id, user.first_name, user.username),
        balance=money(amount),
        title=get_title(earned).label,
    )
    
    # Добавляем место в топе
    if rank:
        balance_text += f"\n🏆 Место в топе: #{rank}"
    
    await send_info_window(session, message, "balance", balance_text)
