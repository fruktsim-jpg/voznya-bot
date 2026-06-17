"""Middleware: «уши» друна — пишет реплики игроков в краткосрочную память.

Чтобы друн отвечал ПО КОНТЕКСТУ и помнил, о чём говорят люди, бот должен
слышать чат. Этот middleware на каждое текстовое сообщение из ЦЕЛЕВОГО чата
сохраняет реплику в ``ai_messages`` (role='chat', ник в meta). Только чтение
мира; запись идёт в ту же сессию и фиксируется DbSessionMiddleware.

Лимиты в пределах разумного:
* только целевой групповой чат (не личка, не другие группы);
* только непустой текст; команды и слишком короткий мусор пропускаем;
* длину реплики режет ``memory.capture_chat`` (анти-раздувание токенов).

Сбой записи НИКОГДА не должен ломать доставку сообщения игроку — всё в
try/except с тихим логом.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logger import get_logger
from app.features.drun import memory as drun_memory

logger = get_logger(__name__)


def _display_name(message: Message) -> str:
    u = message.from_user
    if u is None:
        return "кто-то"
    if u.full_name:
        return u.full_name
    if u.username:
        return f"@{u.username}"
    return f"игрок#{u.id}"


class DrunEarsMiddleware(BaseMiddleware):
    """Сохраняет реплики игроков целевого чата в память друна."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            if isinstance(event, Message):
                await self._maybe_capture(event, data)
        except Exception:  # noqa: BLE001
            logger.debug("drun ears capture failed", exc_info=True)
        return await handler(event, data)

    async def _maybe_capture(self, message: Message, data: dict[str, Any]) -> None:
        settings = get_settings()
        # Слушаем только целевой групповой чат.
        if message.chat.id != settings.chat_id:
            return
        user = message.from_user
        if user is None or user.is_bot:
            return
        text = (message.text or message.caption or "").strip()
        if not text or text.startswith("/"):
            return
        session: AsyncSession | None = data.get("session")
        if session is None:
            return
        await drun_memory.capture_chat(
            session,
            user_id=user.id,
            name=_display_name(message),
            content=text,
        )
