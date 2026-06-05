"""Хендлеры дуэлей: /бой, /го и кнопка принятия боя."""

from __future__ import annotations

import random

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import duel_accept
from app.core.money import money
from app.core.responses import notify_and_cleanup
from app.core.targets import extract_amount_after_target, resolve_target
from app.core.utils import format_cooldown, mention
from app.features.achievements.service import check_award_and_notify
from app.features.duel.service import DuelResult, accept_challenge, create_challenge
from app.models import User
from app.settings import balance, texts

router = Router(name="duel")


async def _finish_duel(answerable, session: AsyncSession, result: DuelResult) -> None:
    """Озвучивает результат завершённого боя и проверяет достижения."""
    winner = await session.get(User, result.winner_id)
    loser = await session.get(User, result.loser_id)
    if winner is None or loser is None:
        return
    winner_mention = mention(winner.user_id, winner.first_name, winner.username)
    loser_mention = mention(loser.user_id, loser.first_name, loser.username)
    # Шапка (кто кого + банк) фиксирована, последняя строка — случайная живая фраза.
    phrase = random.choice(texts.DUEL_PHRASE_VARIANTS).format(
        winner=winner_mention, loser=loser_mention
    )
    await answerable.answer(
        texts.DUEL_RESULT.format(
            winner=winner_mention,
            loser=loser_mention,
            bank=money(result.bank),
            phrase=phrase,
        )
    )

    await check_award_and_notify(
        answerable, session, winner.user_id, winner.first_name, winner.username
    )


@router.message(RuCommand("бой", "duel", "дуэль", "дуэлька"))
async def cmd_duel(message: Message, session: AsyncSession, command_args: str) -> None:
    """Обрабатывает вызов на дуэль: /бой @username ставка ИЛИ /бой ставка (открытый)."""
    user = message.from_user
    if user is None:
        return

    # Пробуем распарсить цель: reply или @username
    target = await resolve_target(session, message, command_args)
    
    # Если цель не найдена — показываем инструкцию
    if target is None:
        await message.answer(texts.DUEL_USAGE)
        return
    
    # Вызов конкретному игроку
    if target.user_id == user.id:
        await message.answer(texts.DUEL_SELF)
        return

    amount_str = extract_amount_after_target(command_args)
    if not amount_str or len(amount_str) > 12 or not amount_str.lstrip("-").isdigit():
        await message.answer(texts.DUEL_USAGE)
        return
    amount = int(amount_str)
    if amount < balance.DUEL_MIN_BET or amount > balance.DUEL_MAX_BET:
        await message.answer(
            texts.DUEL_BAD_AMOUNT.format(min=balance.DUEL_MIN_BET, max=balance.DUEL_MAX_BET)
        )
        return
    
    # Проверка баланса цели ПЕРЕД отправкой вызова
    if target.balance < amount:
        await message.answer(
            texts.DUEL_TARGET_POOR.format(
                mention=mention(target.user_id, target.first_name, target.username),
                balance=money(target.balance)
            )
        )
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
        await message.answer(texts.DUEL_INITIATOR_POOR.format(balance=money(result.balance)))
        return

    await message.answer(
        texts.DUEL_CHALLENGE.format(
            initiator=mention(user.id, user.first_name, user.username),
            target=mention(target.user_id, target.first_name, target.username),
            amount=money(amount),
            minutes=balance.DUEL_EXPIRE_MINUTES,
        ),
        reply_markup=duel_accept(result.pending_id),
    )


@router.message(RuCommand("го", "accept", "go"))
async def cmd_go(message: Message, session: AsyncSession, command_args: str) -> None:
    """Принимает вызов на дуэль командой: /го."""
    user = message.from_user
    if user is None:
        return

    result = await accept_challenge(session, user.id)

    # При отсутствии вызова молчим, чтобы не засорять чат случайным «го».
    if result.status == "no_pending":
        return
    if result.status == "target_poor":
        await message.answer(texts.DUEL_TARGET_POOR.format(balance=money(result.balance)))
        return
    if result.status == "initiator_poor":
        await message.answer(texts.DUEL_INITIATOR_POOR_NOW)
        return

    await _finish_duel(message, session, result)


@router.callback_query(F.data.startswith("duel:accept:"))
async def cb_duel_accept(callback: CallbackQuery, session: AsyncSession) -> None:
    """Принимает вызов на дуэль кнопкой."""
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    pending_id = int(parts[2])

    result = await accept_challenge(session, callback.from_user.id, pending_id=pending_id)

    if result.status == "no_pending":
        await callback.answer(texts.CB_EXPIRED, show_alert=True)
        return
    if result.status == "not_target":
        await callback.answer(texts.CB_NOT_YOURS, show_alert=True)
        return
    if result.status == "target_poor":
        await callback.answer(
            texts.DUEL_TARGET_POOR.format(balance=money(result.balance)), show_alert=True
        )
        return
    if result.status == "initiator_poor":
        await callback.answer(texts.DUEL_INITIATOR_POOR_NOW, show_alert=True)
        return

    # Убираем кнопку, чтобы её нельзя было нажать повторно.
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        await _finish_duel(callback.message, session, result)
    await callback.answer()
