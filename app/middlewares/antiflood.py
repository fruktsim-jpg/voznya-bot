"""Мягкий антифлуд: защита от слишком частых команд одного пользователя.

Это НЕ игровые кулдауны (те хранятся в БД), а защита от случайного
дабл-клика и спама командами. Хранится в памяти процесса — этого достаточно,
так как речь о секундах.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from app.core.filters import looks_like_known_command
from app.settings import balance


class AntiFloodMiddleware(BaseMiddleware):
    """Отсекает команды, поданные чаще, чем раз в ANTIFLOOD_SECONDS."""

    def __init__(self) -> None:
        self._last_command: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            text = event.text or event.caption or ""
            if looks_like_known_command(text):
                user_id = event.from_user.id
                now = time.monotonic()
                last = self._last_command.get(user_id, 0.0)
                if now - last < balance.ANTIFLOOD_SECONDS:
                    # Слишком часто — тихо игнорируем команду.
                    return None
                self._last_command[user_id] = now
        return await handler(event, data)
