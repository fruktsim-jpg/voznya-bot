"""Middleware, ограничивающий работу бота ОДНИМ групповым чатом + личкой.

Бот предназначен для одного группового чата (CHAT_ID): сообщения из других
групп/супергрупп игнорируются. ЛИЧКА (private) теперь открыта ПОЛНОСТЬЮ — в
личных сообщениях работают все команды (ферма, баланс, профиль, кейсы и т.д.),
как и в целевом чате. Так у каждого игрока есть полноценный «личный кабинет» в
боте, плюс продолжают работать онбординг (``/start``), deep-link подарка
(``gift_…``) и привязки сайта (``link_…``).
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

        # ЛИЧКА — открыта полностью: пропускаем ВСЕ личные сообщения и колбэки
        # (ферма, баланс, профиль, кейсы, /start, deep-link подарка/привязки и
        # т.д.). Личка = полноценный «личный кабинет» бота для каждого игрока.
        if is_private:
            return await handler(event, data)

        # Остальное (другие группы/каналы) — игнорируем.
        return None




