"""Хендлеры команд /help и /помощь."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from app.core.filters import RuCommand
from app.settings import balance, texts

router = Router(name="help")


@router.message(RuCommand("help", "помощь", "старт", "start"))
async def cmd_help(message: Message, command_args: str) -> None:
    """Показывает список доступных команд."""
    await message.answer(texts.HELP.format(currency=balance.CURRENCY_NAME))
