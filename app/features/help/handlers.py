"""Хендлеры команд /help и /помощь."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.keyboards import menu_shortcuts, supports_web_app
from app.services.deletion import get_deletion_service
from app.settings import texts
from app.settings import balance

router = Router(name="help")


@router.message(RuCommand("помощь", "help", "старт", "start", "команды", "меню"))

async def cmd_help(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает список команд."""
    user = message.from_user
    if user is None:
        return
    
    sent = await message.answer(
        texts.HELP,
        reply_markup=menu_shortcuts(
            get_settings().website_url,
            prefer_web_app=supports_web_app(message.chat.type),
        ),
    )
    
    # Интеграция с системой "одно активное информационное окно"
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=user.id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
        ttl_seconds=balance.HELP_DELETE_AFTER,
    )
