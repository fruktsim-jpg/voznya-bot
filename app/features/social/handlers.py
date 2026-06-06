"""Социальные приколы Возни: /осеменить.

Это чистые текстовые реакции: никаких механик, ставок, статистики и записи
в БД. Цель — живость чата, а не отдельная игра.

Пасхалка на «да» живёт в marriage/handlers.py (ветка no_pending), потому что
команда «да» принадлежит роутеру браков и срабатывает раньше.
"""

from __future__ import annotations

import random

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.responses import notify_and_cleanup
from app.core.targets import resolve_target
from app.core.utils import mention
from app.settings import texts


router = Router(name="social")



@router.message(
    RuCommand("осеменить", "изнасиловать", "выебать", "наплюхать", "сделать_беременной")
)
async def cmd_inseminate(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Шуточная соц. команда: А осеменил Б. Без механик и сохранения."""
    user = message.from_user
    if user is None:
        return

    target = await resolve_target(session, message, command_args)
    if target is None:
        await notify_and_cleanup(session, message, texts.INSEMINATE_USAGE)
        return

    if target.user_id == user.id:
        await message.answer(texts.INSEMINATE_SELF)
        return

    actor = mention(user.id, user.first_name, user.username)
    victim = mention(target.user_id, target.first_name, target.username)
    await message.answer(
        random.choice(texts.INSEMINATE_VARIANTS).format(actor=actor, target=victim)
    )


