"""Хендлеры команды /баланс."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import quick_actions
from app.core.money import money
from app.core.utils import mention
from app.services.economy import get_balance
from app.settings import texts

router = Router(name="balance")


@router.message(RuCommand("баланс", "balance"))
async def cmd_balance(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает баланс ешек пользователя."""
    user = message.from_user
    if user is None:
        return

    amount = await get_balance(session, user.id)
    await message.answer(
        texts.BALANCE.format(
            mention=mention(user.id, user.first_name, user.username),
            balance=money(amount),
        ),
        reply_markup=quick_actions(),
    )
