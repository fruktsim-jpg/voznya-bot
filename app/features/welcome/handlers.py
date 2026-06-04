"""Приветствие новых участников чата.

Сообщение приветствия автоматически удаляется через минуту, текст
выбирается случайно из нескольких коротких вариантов.
"""

from __future__ import annotations

import random

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import mention
from app.services.deletion import get_deletion_service
from app.settings import balance, texts

router = Router(name="welcome")


@router.message(F.new_chat_members)
async def on_new_members(message: Message, session: AsyncSession) -> None:
    """Приветствует каждого нового участника отдельным сообщением."""
    deletion = get_deletion_service()

    for member in message.new_chat_members or []:
        if member.is_bot:
            continue
        text = random.choice(texts.WELCOME_VARIANTS).format(
            mention=mention(member.id, member.first_name, member.username)
        )
        sent = await message.answer(text, disable_web_page_preview=False)
        await deletion.schedule(
            session, sent.chat.id, sent.message_id, balance.WELCOME_DELETE_AFTER
        )

    # Убираем служебное сообщение о входе, чтобы чат оставался чистым.
    await deletion.schedule(
        session, message.chat.id, message.message_id, balance.WELCOME_DELETE_AFTER
    )
