"""Middleware, ограничивающий работу бота одним чатом.

Бот предназначен для одного чата (CHAT_ID). Сообщения из других групп
игнорируются. В личке (private) бот раньше отвечал ТОЛЬКО админам и на
deep-link привязки — из-за этого обычные игроки не могли пройти онбординг и,
например, принять подаренный подарок. Теперь личка открыта для онбординга:
пропускаем ``/start`` (в т.ч. deep-link подарка ``gift_…`` и привязки
``link_…``) и ``/help`` от кого угодно, а также все ЛС-сообщения админов. Это
нужно, чтобы получатель подарка мог запустить бота и забрать его.
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

        # Личка: онбординг открыт для всех. Пропускаем /start (в т.ч. deep-link
        # подарка gift_… и привязки link_…) и /help — чтобы новый игрок мог
        # запустить бота и, например, забрать подаренный подарок. Колбэки в
        # личке (кнопки приветствия/claim) тоже пропускаем — иначе кнопки
        # онбординга не работали бы у не-админов.
        if is_private and isinstance(event, Message) and _is_onboarding_cmd(event.text):
            return await handler(event, data)
        if is_private and isinstance(event, CallbackQuery):
            return await handler(event, data)

        # Остальное игнорируем.
        return None


def _command_name(text: str | None) -> str | None:
    """Извлекает имя команды (без слеша и @bot) из текста сообщения."""
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    command = parts[0]
    if command.startswith("/"):
        command = command[1:]
    if "@" in command:
        command = command.split("@", 1)[0]
    return command.lower() or None


def _is_onboarding_cmd(text: str | None) -> bool:
    """True для команд онбординга в личке: /start (с любым payload) и /help."""
    return _command_name(text) in {"start", "help", "помощь", "старт"}


def _is_start_link(text: str | None) -> bool:
    """True для сообщения вида ``/start link_<token>`` (deep-link привязки)."""
    if _command_name(text) != "start":
        return False
    parts = (text or "").strip().split(maxsplit=1)
    return len(parts) == 2 and parts[1].strip().startswith("link_")



