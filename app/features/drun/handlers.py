"""Команды Тёмного друна.

``/друн`` (admin) — попросить друна бросить наблюдение в чат. MVP-триггер:
друн смотрит на мир/события и говорит в образе. Доступно только админам, чтобы
на старте контролировать включение и расход токенов.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.targets import resolve_target
from app.features.drun import service as drun_service

router = Router(name="drun")


def _is_admin(message: Message) -> bool:
    return (
        message.from_user is not None
        and get_settings().is_admin(message.from_user.id)
    )


@router.message(RuCommand("друн", "drun"))
async def cmd_drun(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """/друн [@игрок] — друн бросает наблюдение (про мир или про игрока)."""
    if not _is_admin(message):
        return

    # Необязательная цель: /друн @user → наблюдение про конкретного игрока.
    subject_id: int | None = None
    if command_args.strip():
        target = await resolve_target(session, message, command_args)
        if target is not None:
            subject_id = target.user_id

    result = await drun_service.observe(session, subject_id=subject_id)
    if not result.ok:
        # Тихо для обычной работы; админу подскажем причину.
        if result.error == "disabled":
            await message.reply("Друн молчит: ИИ выключен или не настроен.")
        else:
            await message.reply(f"Друн поперхнулся: {result.error}")
        return

    # Сессию фиксирует DbSessionMiddleware после успешной обработки.
    await message.answer(result.text)
