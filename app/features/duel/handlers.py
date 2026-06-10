"""Хендлеры дуэлей: /бой, /го и кнопка принятия боя."""

from __future__ import annotations

import random

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime

from aiogram import Bot

from app.core.db import get_sessionmaker
from app.core.filters import RuCommand
from app.core.keyboards import duel_accept
from app.core.money import money
from app.core.responses import notify_and_cleanup
from app.core.scheduler import get_scheduler
from app.core.targets import extract_amount_after_target, resolve_target
from app.core.utils import format_cooldown, mention, now_utc
from app.features.achievements.service import check_and_award, format_unlock_notification
from app.features.duel.service import (
    DuelResult,
    accept_challenge,
    create_challenge,
    decline_challenge,
    expire_challenge_if_pending,
)

from app.models import User
from app.settings import balance, texts

router = Router(name="duel")


async def _expire_and_cleanup(
    bot: Bot, chat_id: int, message_id: int, pending_id: int
) -> None:
    """Фоновая задача: по истечении срока гасит непринятый вызов и убирает его
    сообщение из чата, чтобы мёртвые вызовы не висели.

    Если вызов уже приняли/отклонили — ничего не делаем (сообщение там уже
    обновлено результатом боя или отказа).
    """
    async with get_sessionmaker()() as session:
        expired = await expire_challenge_if_pending(session, pending_id)
        await session.commit()
    if not expired:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:  # noqa: BLE001
        # Сообщение уже удалено/недоступно — это нормально.
        pass


def _schedule_duel_cleanup(
    bot: Bot,
    chat_id: int,
    message_id: int,
    pending_id: int,
    expires_at: datetime,
) -> None:
    """Планирует автоудаление непринятого вызова на момент его протухания."""
    from datetime import timedelta

    get_scheduler().add_job(
        _expire_and_cleanup,
        trigger="date",
        run_date=max(expires_at, now_utc() + timedelta(seconds=1)),
        args=[bot, chat_id, message_id, pending_id],
        id=f"duel_expire_{pending_id}",
        replace_existing=True,
        misfire_grace_time=3600,
    )



async def _finish_duel(
    answerable, session: AsyncSession, result: DuelResult, *, edit_source: bool = False
) -> None:
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
    parts = [
        texts.DUEL_RESULT.format(
            winner=winner_mention,
            loser=loser_mention,
            bank=money(result.bank),
            phrase=phrase,
        )
    ]

    winner_achievements = await check_and_award(session, winner.user_id)
    winner_unlock = format_unlock_notification(
        winner.user_id, winner.first_name, winner.username, winner_achievements
    )
    if winner_unlock:
        parts.append(winner_unlock)

    # Проигравший тоже меняет счётчики (duels_lost, duel_loss_streak), поэтому
    # его достижения — в т.ч. секретный «Мешок» за 5 поражений подряд — нужно
    # проверять СРАЗУ здесь, а не ждать его следующего действия.
    loser_achievements = await check_and_award(session, loser.user_id)
    loser_unlock = format_unlock_notification(
        loser.user_id, loser.first_name, loser.username, loser_achievements
    )
    if loser_unlock:
        parts.append(loser_unlock)

    # Повышения ранга MMR агрегируем после дуэльных и achievement-MMR начислений,
    # чтобы один бой давал одно итоговое сообщение.
    from app.features.mmr.service import detect_rankup, format_rankup

    winner_rankup = await detect_rankup(session, winner.user_id, result.winner_mmr_before)
    loser_rankup = await detect_rankup(session, loser.user_id, result.loser_mmr_before)
    if winner_rankup is not None:
        parts.append(format_rankup(winner_mention, winner_rankup))
    if loser_rankup is not None:
        parts.append(format_rankup(loser_mention, loser_rankup))

    final_text = "\n\n".join(parts)
    if edit_source:
        try:
            await answerable.edit_text(final_text)
            return
        except Exception:  # noqa: BLE001
            pass
    await answerable.answer(final_text)


async def _edit_callback_message(callback: CallbackQuery, text: str) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text)
    except Exception:  # noqa: BLE001
        pass


@router.message(RuCommand("бой", "duel", "дуэль", "дуэлька"))
async def cmd_duel(message: Message, session: AsyncSession, command_args: str) -> None:
    """Обрабатывает вызов на дуэль: /бой @username ставка ИЛИ /бой ставка (открытый)."""
    user = message.from_user
    if user is None:
        return

    # Пробуем распарсить цель: reply или @username
    target = await resolve_target(session, message, command_args)
    
    # Если цель не найдена, проверяем, может это открытый вызов (просто /бой 50)
    if target is None:
        # Пробуем распарсить как открытый вызов
        amount_str = command_args.strip().split()[0] if command_args.strip() else ""
        if not amount_str or len(amount_str) > 12 or not amount_str.lstrip("-").isdigit():
            await notify_and_cleanup(session, message, texts.DUEL_USAGE)
            return
        amount = int(amount_str)
        if amount < balance.DUEL_MIN_BET or amount > balance.DUEL_MAX_BET:
            await notify_and_cleanup(
                session,
                message,
                texts.DUEL_BAD_AMOUNT.format(min=balance.DUEL_MIN_BET, max=balance.DUEL_MAX_BET),
            )
            return

        
        # Создаём открытый вызов (target_id=None)
        result = await create_challenge(
            session, user.id, None, amount, message.chat.id
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

        # Открытый вызов: кнопки отказа нет (отказываться некому — зовём весь чат).
        sent = await message.answer(
            texts.DUEL_OPEN_CHALLENGE.format(
                initiator=mention(user.id, user.first_name, user.username),
                amount=money(amount),
                minutes=balance.DUEL_EXPIRE_MINUTES,
            ),
            reply_markup=duel_accept(result.pending_id, with_decline=False),
        )
        if result.expires_at is not None:
            _schedule_duel_cleanup(
                message.bot, sent.chat.id, sent.message_id,
                result.pending_id, result.expires_at,
            )
        return

    
    # Вызов конкретному игроку
    if target.user_id == user.id:
        await message.answer(texts.DUEL_SELF)
        return

    amount_str = extract_amount_after_target(command_args)
    if not amount_str or len(amount_str) > 12 or not amount_str.lstrip("-").isdigit():
        await notify_and_cleanup(session, message, texts.DUEL_USAGE)
        return
    amount = int(amount_str)
    if amount < balance.DUEL_MIN_BET or amount > balance.DUEL_MAX_BET:
        await notify_and_cleanup(
            session,
            message,
            texts.DUEL_BAD_AMOUNT.format(min=balance.DUEL_MIN_BET, max=balance.DUEL_MAX_BET),
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

    sent = await message.answer(
        texts.DUEL_CHALLENGE.format(
            initiator=mention(user.id, user.first_name, user.username),
            target=mention(target.user_id, target.first_name, target.username),
            amount=money(amount),
            minutes=balance.DUEL_EXPIRE_MINUTES,
        ),
        reply_markup=duel_accept(result.pending_id),
    )
    if result.expires_at is not None:
        _schedule_duel_cleanup(
            message.bot, sent.chat.id, sent.message_id,
            result.pending_id, result.expires_at,
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
        await _finish_duel(callback.message, session, result, edit_source=True)
    await callback.answer()


@router.callback_query(F.data.startswith("duel:decline:"))
async def cb_duel_decline(callback: CallbackQuery, session: AsyncSession) -> None:
    """Отклоняет вызов на дуэль кнопкой и закрывает запись."""
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    pending_id = int(parts[2])

    result = await decline_challenge(session, callback.from_user.id, pending_id)

    if result.status == "no_pending":
        await callback.answer(texts.CB_EXPIRED, show_alert=True)
        return
    if result.status == "not_target":
        await callback.answer(texts.CB_NOT_YOURS, show_alert=True)
        return

    # Закрываем вызов в том же сообщении, чтобы отказ не добавлял лишний пост.
    if callback.message is not None:
        decliner = await session.get(User, result.decliner_id)
        initiator = await session.get(User, result.initiator_id)
        decliner_mention = (
            mention(decliner.user_id, decliner.first_name, decliner.username)
            if decliner
            else "Боец"
        )
        initiator_mention = (
            mention(initiator.user_id, initiator.first_name, initiator.username)
            if initiator
            else "Боец"
        )
        await _edit_callback_message(
            callback,
            texts.DUEL_DECLINED.format(
                decliner=decliner_mention, initiator=initiator_mention
            ),
        )
    await callback.answer()
