"""Хендлеры команды /ферма."""

from __future__ import annotations

import random

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.money import money

from app.core.responses import notify_and_cleanup
from app.core.utils import format_cooldown, mention
from app.features.achievements.service import check_award_and_notify
from app.features.farm.service import do_farm
from app.settings import texts

router = Router(name="farm")


def render_farm_result(result, who: str = "") -> str:
    """Формирует текст результата фермы: реплика из пула + строка баланса."""
    if result.outcome == "loss":
        line = random.choice(texts.FARM_LOSS_VARIANTS).format(
            mention=who, amount=money(abs(result.amount))
        )
    elif result.amount == 0:
        line = random.choice(texts.FARM_ZERO_VARIANTS).format(mention=who)
    else:
        line = random.choice(texts.FARM_GAIN_VARIANTS).format(
            mention=who, amount=money(result.amount)
        )
        if result.streak_percent > 0:
            line += texts.FARM_STREAK_SUFFIX.format(
                days=result.streak, percent=result.streak_percent
            )
    return line + texts.FARM_BALANCE.format(balance=money(result.balance))


@router.message(RuCommand("ферма", "farm", "фарм"))
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

    who = mention(user.id, user.first_name, user.username)
    # MMR снимаем ДО начислений этого апдейта, чтобы поймать повышение ранга.
    from app.features.mmr.service import announce_rankup_if_any
    from app.repositories.mmr import get_mmr

    mmr_before = await get_mmr(session, user.id)

    await message.answer(render_farm_result(result, who))
    await check_award_and_notify(message, session, user.id, user.first_name, user.username)
    await announce_rankup_if_any(message, session, user.id, who, mmr_before)
