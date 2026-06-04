"""Общие помощники для ответов и поддержания чистоты чата."""

from __future__ import annotations

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.deletion import get_deletion_service
from app.settings import balance


async def notify_and_cleanup(
    session: AsyncSession,
    message: Message,
    text: str,
    delete_after: float = balance.COOLDOWN_NOTICE_DELETE_AFTER,
) -> None:
    """Отправляет временное уведомление и убирает за собой мусор.

    Удаляет сообщение пользователя и (через ``delete_after`` секунд) ответ бота.
    Используется для уведомлений о кулдауне и других «технических» ответов,
    чтобы чат оставался чистым.
    """
    deletion = get_deletion_service()
    reply = await message.answer(text)

    # Сообщение пользователя удаляем почти сразу.
    await deletion.schedule(session, message.chat.id, message.message_id, 1)
    # Ответ бота — спустя заданное время.
    await deletion.schedule(session, reply.chat.id, reply.message_id, delete_after)
