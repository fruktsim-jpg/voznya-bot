"""Middleware, ограничивающий работу бота одним чатом.

Бот предназначен для одного чата (CHAT_ID). Сообщения из других групп
игнорируются. Личные сообщения от администраторов пропускаются — чтобы
админ мог управлять ботом в личке при необходимости.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, TelegramObject

from app.config import get_settings


class ChatFilterMiddleware(BaseMiddleware):
    """Пропускает только события из целевого чата (и личку админов)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        settings = get_settings()

        chat_id: int | None = None
        user_id: int | None = None
        is_private = False

        if isinstance(event, Message):
            chat_id = event.chat.id
            is_private = event.chat.type == "private"
            if event.from_user is not None:
                user_id = event.from_user.id
        elif isinstance(event, ChatMemberUpdated):
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            if event.message is not None:
                chat_id = event.message.chat.id
                is_private = event.message.chat.type == "private"

        # Целевой чат — всегда пропускаем.
        if chat_id == settings.chat_id:
            return await handler(event, data)

        # Личка администратора — пропускаем (для управления ботом).
        if is_private and user_id is not None and settings.is_admin(user_id):
            return await handler(event, data)

        # Остальное игнорируем.
        return None
