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


async def send_leaderboard(
    session: AsyncSession,
    message: Message,
    leaderboard_type: str,
    text: str,
    *,
    reply_markup=None,
) -> Message:
    """Единый шаблон вывода рейтинга/топа (P1-11).

    Все ранговые топы (богачи, неделя, семьи, MMR, репутация, сезон) держат
    одно активное окно на чат и тип: новое окно заменяет предыдущее того же
    типа, а команда игрока убирается из чата. Возвращает отправленное
    сообщение бота, чтобы вызывающий код мог при необходимости его дополнить.
    """
    deletion = get_deletion_service()
    sent = await message.answer(text, reply_markup=reply_markup)
    await deletion.replace_leaderboard_message(
        message.chat.id,
        leaderboard_type,
        message.message_id,
        sent.message_id,
    )
    await deletion.schedule(session, message.chat.id, message.message_id, 1)
    return sent
