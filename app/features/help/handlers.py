"""Хендлеры команд /help и /помощь."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.services.deletion import get_deletion_service
from app.settings import balance, texts

router = Router(name="help")


@router.message(RuCommand("помощь", "help", "старт", "start"))
async def cmd_help(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает список команд и удаляет его через несколько минут."""
    sent = await message.answer(texts.HELP)
    # Помощь не несёт долгосрочной ценности — убираем, чтобы не засорять чат.
    await get_deletion_service().schedule(
        session, sent.chat.id, sent.message_id, balance.HELP_DELETE_AFTER
    )
