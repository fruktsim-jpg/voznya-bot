"""Хендлеры дуэлей: /бой и /го."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.responses import notify_and_cleanup
from app.core.targets import extract_amount_after_target, resolve_target
from app.core.utils import format_cooldown, mention
from app.features.duel.service import accept_challenge, create_challenge
from app.models import User
from app.settings import balance, texts

router = Router(name="duel")


@router.message(RuCommand("бой", "duel"))
async def cmd_duel(message: Message, session: AsyncSession, command_args: str) -> None:
    """Обрабатывает вызов на дуэль: /бой @username ставка."""
    user = message.from_user
    if user is None:
        return

    target = await resolve_target(session, message, command_args)
    if target is None:
        await message.answer(texts.DUEL_USAGE)
        return
    if target.user_id == user.id:
        await message.answer(texts.DUEL_SELF)
        return

    amount_str = extract_amount_after_target(command_args)
    if not amount_str or not amount_str.lstrip("-").isdigit():
        await message.answer(texts.DUEL_USAGE)
        return
    amount = int(amount_str)
    if amount < balance.DUEL_MIN_BET:
        await message.answer(texts.DUEL_BAD_AMOUNT.format(min=balance.DUEL_MIN_BET))
        return

    result = await create_challenge(
        session, user.id, target.user_id, amount, message.chat.id
    )

    if result.status == "cooldown":
        await notify_and_cleanup(
            session,
            message,
            texts.COOLDOWN_NOTICE.format(time=format_cooldown(result.remaining)),
        )
        return
    if result.status == "poor":
        await message.answer(
            texts.DUEL_INITIATOR_POOR.format(
                currency=balance.CURRENCY_NAME, balance=result.balance
            )
        )
        return

    await message.answer(
        texts.DUEL_CHALLENGE.format(
            initiator=mention(user.id, user.first_name, user.username),
            target=mention(target.user_id, target.first_name, target.username),
            amount=amount,
            currency=balance.CURRENCY_NAME,
            minutes=balance.DUEL_EXPIRE_MINUTES,
        )
    )


@router.message(RuCommand("го", "go"))
async def cmd_go(message: Message, session: AsyncSession, command_args: str) -> None:
    """Принимает вызов на дуэль: /го."""
    user = message.from_user
    if user is None:
        return

    result = await accept_challenge(session, user.id)

    if result.status == "no_pending":
        await message.answer(texts.DUEL_NO_PENDING)
        return
    if result.status == "target_poor":
        await message.answer(
            texts.DUEL_TARGET_POOR.format(
                currency=balance.CURRENCY_NAME, balance=result.balance
            )
        )
        return
    if result.status == "initiator_poor":
        await message.answer(
            texts.DUEL_INITIATOR_POOR_NOW.format(currency=balance.CURRENCY_NAME)
        )
        return

    winner = await session.get(User, result.winner_id)
    loser = await session.get(User, result.loser_id)
    assert winner is not None and loser is not None

    await message.answer(
        texts.DUEL_RESULT.format(
            winner=mention(winner.user_id, winner.first_name, winner.username),
            loser=mention(loser.user_id, loser.first_name, loser.username),
            bank=result.bank,
            currency=balance.CURRENCY_NAME,
        )
    )
