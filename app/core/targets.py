"""Определение целевого пользователя команды.

Поддерживаются два способа указать пользователя (как договорились в ТЗ):
1. ответ (reply) на сообщение нужного человека — самый надёжный способ;
2. упоминание ``@username`` в тексте команды.

Дополнительно понимается числовой ID.
"""

from __future__ import annotations

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories import users as users_repo


async def resolve_target(
    session: AsyncSession, message: Message, args: str
) -> User | None:
    """Возвращает целевого пользователя команды или None.

    Приоритет — у reply: если команда отправлена ответом на сообщение
    реального пользователя (не бота), берём именно его.
    """
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None and not reply.from_user.is_bot:
        src = reply.from_user
        await users_repo.upsert_user(
            session, src.id, src.username, src.first_name, touch_activity=False
        )
        return await users_repo.get_user(session, src.id)

    if not args:
        return None

    token = args.split()[0]
    if token.startswith("@"):
        return await users_repo.get_user_by_username(session, token)

    if token.lstrip("-").isdigit():
        return await users_repo.get_user(session, int(token))

    return None


def extract_amount_after_target(args: str) -> str | None:
    """Достаёт сумму ставки из аргументов вида «@user 25» или «25».

    Возвращает строку с числом (валидацию делает вызывающий код) или None.
    """
    if not args:
        return None
    parts = args.split()
    # Если первый токен — упоминание/ID, число должно быть вторым.
    if parts[0].startswith("@") or parts[0].lstrip("-").isdigit() and len(parts) > 1:
        return parts[1] if len(parts) > 1 else None
    # Иначе сумма — первый токен (цель указана через reply).
    return parts[0]
