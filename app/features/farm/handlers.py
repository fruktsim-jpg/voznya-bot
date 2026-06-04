"""Хендлеры команды /ферма."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.responses import notify_and_cleanup
from app.core.utils import format_cooldown, mention
from app.features.farm.service import do_farm
from app.settings import balance, texts

router = Router(name="farm")


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

    who = mention(user.id, user.first_name, user.username)

    if result.outcome == "loss":
        text = texts.FARM_LOSS.format(
            mention=who,
            amount=abs(result.amount),
            currency=balance.CURRENCY_NAME,
            balance=result.balance,
        )
    elif result.amount == 0:
        text = texts.FARM_ZERO.format(mention=who, balance=result.balance)
    else:
        streak_suffix = ""
        if result.streak_percent > 0:
            streak_suffix = texts.FARM_STREAK_SUFFIX.format(
                days=result.streak, percent=result.streak_percent
            )
        text = texts.FARM_GAIN.format(
            mention=who,
            amount=result.amount,
            currency=balance.CURRENCY_NAME,
            streak=streak_suffix,
            balance=result.balance,
        )

    await message.answer(text)
