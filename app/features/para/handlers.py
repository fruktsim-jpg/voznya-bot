"""Хендлеры команды /пара."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.money import money
from app.core.utils import display_name
from app.features.achievements.service import check_award_and_notify
from app.features.para.service import get_or_choose_para
from app.models import User
from app.settings import balance, texts

router = Router(name="para")


async def _mention_of(session: AsyncSession, user_id: int) -> str:
    # Имя без пинга: «Пара дня» — публичная номинация, не звоним людям
    # уведомлением каждый раз, когда кто-то вызвал команду.
    user = await session.get(User, user_id)
    if user is None:
        return "кто-то"
    return display_name(user.first_name, user.username)



@router.message(RuCommand("пара", "couple", "para"))
async def cmd_para(message: Message, session: AsyncSession, command_args: str) -> None:
    """Обрабатывает команду /пара."""
    user = message.from_user
    if user is None:
        return

    result = await get_or_choose_para(session, user.id)

    if result.status == "not_enough":
        await message.answer(
            texts.NOMINATION_NOT_ENOUGH.format(min=balance.NOMINATION_MIN_CANDIDATES)
        )
        return

    first = await _mention_of(session, result.first_id)
    second = await _mention_of(session, result.second_id)

    if result.status == "chosen":
        text = texts.PARA_CHOSEN.format(
            first=first,
            second=second,
            bonus=money(result.opener_bonus),
        )
    else:
        text = texts.PARA_TODAY.format(first=first, second=second)

    await message.answer(text)

    # При фактическом выборе открывший получает продуктивный бонус
    # (total_earned), который может открыть экономическое достижение —
    # проверяем СРАЗУ, чтобы ачивка не «висела» до следующего действия.
    if result.status == "chosen":
        await check_award_and_notify(
            message, session, user.id, user.first_name, user.username
        )
