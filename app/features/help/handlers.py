"""Хендлеры команд /help и /помощь."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from app.core.filters import RuCommand
from app.settings import texts

router = Router(name="help")


@router.message(RuCommand("помощь", "help", "старт", "start"))
async def cmd_help(message: Message, command_args: str) -> None:
    """Показывает список доступных команд."""
    await message.answer(texts.HELP)
