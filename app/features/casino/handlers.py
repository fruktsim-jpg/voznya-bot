"""Хендлеры команды /казино."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.responses import notify_and_cleanup
from app.core.utils import format_cooldown, mention
from app.features.casino.service import play_casino
from app.settings import balance, texts

router = Router(name="casino")


def _format_multiplier(value: float) -> str:
    """Красиво форматирует множитель (1.5 → «1.5», 2.0 → «2»)."""
    return str(int(value)) if value.is_integer() else str(value)


@router.message(RuCommand("казино", "casino"))
async def cmd_casino(message: Message, session: AsyncSession, command_args: str) -> None:
    """Обрабатывает команду /казино сумма."""
    user = message.from_user
    if user is None:
        return

    arg = command_args.split()[0] if command_args else ""
    if not arg:
        await message.answer(texts.CASINO_USAGE)
        return
    if not arg.lstrip("-").isdigit():
        await message.answer(
            texts.CASINO_BAD_AMOUNT.format(
                min=balance.CASINO_MIN_BET, max=balance.CASINO_MAX_BET
            )
        )
        return

    bet = int(arg)
    if bet < balance.CASINO_MIN_BET or bet > balance.CASINO_MAX_BET:
        await message.answer(
            texts.CASINO_BAD_AMOUNT.format(
                min=balance.CASINO_MIN_BET, max=balance.CASINO_MAX_BET
            )
        )
        return

    result = await play_casino(session, user.id, bet)
    who = mention(user.id, user.first_name, user.username)

    if result.status == "cooldown":
        await notify_and_cleanup(
            session,
            message,
            texts.COOLDOWN_NOTICE.format(time=format_cooldown(result.remaining)),
        )
        return

    if result.status == "not_enough":
        await message.answer(
            texts.CASINO_NOT_ENOUGH.format(
                currency=balance.CURRENCY_NAME, balance=result.balance
            )
        )
        return

    if result.outcome == "loss":
        text = texts.CASINO_LOSS.format(mention=who, bet=result.bet, balance=result.balance)
    elif result.outcome == "jackpot":
        text = texts.CASINO_JACKPOT.format(
            mention=who,
            bet=result.bet,
            multiplier=_format_multiplier(result.multiplier),
            payout=result.payout,
            currency=balance.CURRENCY_NAME,
            balance=result.balance,
        )
    else:
        text = texts.CASINO_WIN.format(
            mention=who,
            bet=result.bet,
            multiplier=_format_multiplier(result.multiplier),
            payout=result.payout,
            currency=balance.CURRENCY_NAME,
            balance=result.balance,
        )

    await message.answer(text)
