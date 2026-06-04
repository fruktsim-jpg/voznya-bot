"""Хендлеры команды /баланс."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import quick_actions
from app.core.money import money
from app.core.utils import mention
from app.repositories import users as users_repo
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
    await message.answer(
        texts.BALANCE.format(
            mention=mention(user.id, user.first_name, user.username),
            balance=money(amount),
            title=get_title(earned).label,
        ),
        reply_markup=quick_actions(),
    )
