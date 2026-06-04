"""Хендлеры команды /ферма."""

from __future__ import annotations

import random

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import quick_actions
from app.core.money import money
from app.core.responses import notify_and_cleanup
from app.core.utils import format_cooldown
from app.features.achievements.service import check_award_and_notify
from app.features.farm.service import do_farm
from app.settings import texts

router = Router(name="farm")


def render_farm_result(result, who: str = "") -> str:
    """Формирует короткий текст результата фермы (случайный из пула)."""
    if result.outcome == "loss":
        return random.choice(texts.FARM_LOSS_VARIANTS).format(
            amount=money(abs(result.amount))
        )
    if result.amount == 0:
        return random.choice(texts.FARM_ZERO_VARIANTS)
    text = random.choice(texts.FARM_GAIN_VARIANTS).format(amount=money(result.amount))
    if result.streak_percent > 0:
        text += texts.FARM_STREAK_SUFFIX.format(
            days=result.streak, percent=result.streak_percent
        )
    return text


@router.message(RuCommand("ферма", "farm"))
async def cmd_farm(message: Message, session: AsyncSession, command_args: str) -> None:
    """Обрабатывает команду /ферма."""
    user = message.from_user
    if user is None:
        return

    result = await do_farm(session, user.id)

    if result.on_cooldown:
        await notify_and_cleanup(
            session,
            message,
            texts.COOLDOWN_NOTICE.format(time=format_cooldown(result.remaining)),
        )
        return

    await message.answer(render_farm_result(result), reply_markup=quick_actions())
    await check_award_and_notify(message, session, user.id, user.first_name, user.username)
