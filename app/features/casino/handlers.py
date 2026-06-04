"""Хендлеры команды /казино и кнопки повтора ставки."""

from __future__ import annotations

import random

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import casino_again
from app.core.money import money
from app.core.responses import notify_and_cleanup
from app.core.utils import format_cooldown, mention
from app.features.achievements.service import check_award_and_notify, notify_specific
from app.features.casino.service import CasinoResult, play_casino
from app.settings import balance, texts

router = Router(name="casino")


async def _award_casino_events(
    answerable, session, user, result: CasinoResult
) -> None:
    """Выдаёт событийные достижения казино (джекпот, ва-банк)."""
    if result.jackpot:
        await notify_specific(answerable, session, user.id, user.first_name, user.username, "catushka")
    if result.all_in and result.outcome == "loss":
        await notify_specific(answerable, session, user.id, user.first_name, user.username, "last_dep")


def _format_multiplier(value: float) -> str:
    """Красиво форматирует множитель (1.5 → «1.5», 2.0 → «2»)."""
    return str(int(value)) if value.is_integer() else str(value)


def _render_result(result: CasinoResult, who: str = "") -> str:
    """Формирует короткий текст результата игры (случайная реплика из пула)."""
    if result.outcome == "loss":
        return texts.CASINO_LOSS.format(
            phrase=random.choice(texts.CASINO_LOSS_VARIANTS),
            bet=money(result.bet),
            balance=money(result.balance),
        )
    if result.outcome == "jackpot":
        return texts.CASINO_JACKPOT.format(
            multiplier=_format_multiplier(result.multiplier),
            net=money(result.net),
            balance=money(result.balance),
        )
    return texts.CASINO_WIN.format(
        phrase=random.choice(texts.CASINO_WIN_VARIANTS),
        net=money(result.net),
        balance=money(result.balance),
    )


def _parse_bet(arg: str) -> int | None:
    """Парсит ставку. Возвращает None при некорректном вводе."""
    if not arg or len(arg) > 12 or not arg.lstrip("-").isdigit():
        return None
    return int(arg)


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

    bet = _parse_bet(arg)
    if bet is None or bet < balance.CASINO_MIN_BET or bet > balance.CASINO_MAX_BET:
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
            texts.CASINO_NOT_ENOUGH.format(balance=money(result.balance))
        )
        return

    await message.answer(
        _render_result(result, who), reply_markup=casino_again(user.id, bet)
    )
    await check_award_and_notify(message, session, user.id, user.first_name, user.username)
    await _award_casino_events(message, session, user, result)


@router.callback_query(F.data.startswith("casino:repeat:"))
async def cb_casino_repeat(callback: CallbackQuery, session: AsyncSession) -> None:
    """Повторяет ставку из кнопки (только для владельца кнопки)."""
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    owner_id, bet = int(parts[2]), int(parts[3])

    if callback.from_user.id != owner_id:
        await callback.answer(texts.CB_NOT_YOURS, show_alert=True)
        return

    result = await play_casino(session, owner_id, bet)
    who = mention(
        callback.from_user.id, callback.from_user.first_name, callback.from_user.username
    )

    if result.status == "cooldown":
        await callback.answer(
            texts.COOLDOWN_NOTICE.format(time=format_cooldown(result.remaining)),
            show_alert=True,
        )
        return
    if result.status == "not_enough":
        await callback.answer(
            texts.CASINO_NOT_ENOUGH.format(balance=money(result.balance)), show_alert=True
        )
        return

    if callback.message is not None:
        await callback.message.answer(
            _render_result(result, who), reply_markup=casino_again(owner_id, bet)
        )
        await check_award_and_notify(
            callback.message,
            session,
            owner_id,
            callback.from_user.first_name,
            callback.from_user.username,
        )
        await _award_casino_events(callback.message, session, callback.from_user, result)
    await callback.answer()
