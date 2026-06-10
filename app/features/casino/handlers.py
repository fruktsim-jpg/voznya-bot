"""Хендлеры команды /казино и кнопки повтора ставки."""

from __future__ import annotations

import asyncio
import random

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession


from app.core.filters import RuCommand
from app.core.money import money

from app.core.responses import notify_and_cleanup
from app.core.utils import format_cooldown, mention
from app.features.achievements.service import (
    award_specific,
    check_and_award,
    format_unlock_notification,
)
from app.features.casino.service import CasinoResult, play_casino
from app.settings import balance, dynamic, texts


router = Router(name="casino")


async def _award_casino_events(session, user, result: CasinoResult) -> list:
    """Выдаёт событийные достижения казино и возвращает новые ачивки."""
    achievements = []
    if result.jackpot:
        unlocked = await award_specific(session, user.id, "catushka")
        if unlocked is not None:
            achievements.append(unlocked)
    if result.all_in and result.outcome == "loss":
        unlocked = await award_specific(session, user.id, "last_dep")
        if unlocked is not None:
            achievements.append(unlocked)
    return achievements


def _format_multiplier(value: float) -> str:
    """Красиво форматирует множитель (1.5 → «1.5», 2.0 → «2»)."""
    return str(int(value)) if value.is_integer() else str(value)


def _render_result(result: CasinoResult, who: str = "") -> str:
    """Формирует короткий текст результата игры (случайная реплика из пула)."""
    if result.outcome == "loss":
        # Сумма теперь внутри самой фразы (вайб лудки), поэтому сначала
        # подставляем {bet} в выбранную реплику, затем оборачиваем балансом.
        phrase = random.choice(texts.CASINO_LOSS_VARIANTS).format(bet=money(result.bet))
        return texts.CASINO_LOSS.format(phrase=phrase, balance=money(result.balance))
    if result.outcome == "jackpot":
        return texts.CASINO_JACKPOT.format(
            multiplier=_format_multiplier(result.multiplier),
            net=money(result.net),
            balance=money(result.balance),
        )
    phrase = random.choice(texts.CASINO_WIN_VARIANTS).format(net=money(result.net))
    return texts.CASINO_WIN.format(phrase=phrase, balance=money(result.balance))



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
        await notify_and_cleanup(session, message, texts.CASINO_USAGE)
        return

    # Лимиты ставки редактируются из админки (app_settings) без деплоя;
    # если ключей нет — берём дефолты из balance.py.
    min_bet = await dynamic.get_int(session, "casino.min_bet", balance.CASINO_MIN_BET)
    max_bet = await dynamic.get_int(session, "casino.max_bet", balance.CASINO_MAX_BET)

    bet = _parse_bet(arg)
    if bet is None or bet < min_bet or bet > max_bet:
        await notify_and_cleanup(
            session,
            message,
            texts.CASINO_BAD_AMOUNT.format(min=min_bet, max=max_bet),
        )
        return



    result = await play_casino(session, user.id, bet)
    who = mention(user.id, user.first_name, user.username)

    # Кулдаун/нехватка средств — игра не состоялась, барабан не крутим.
    # Эти сообщения короткие и самоудаляются, отдельное «ожидание» тут лишнее.
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

    # Игра состоялась: показываем ожидание и редактируем ЭТО ЖЕ сообщение
    # результатом. Так итог живёт в одном сообщении, без лишнего спама в чате.
    spinning = await message.answer(texts.CASINO_SPINNING)
    await asyncio.sleep(1.0)
    parts = [_render_result(result, who)]
    achievements = await check_and_award(session, user.id)
    achievements.extend(await _award_casino_events(session, user, result))
    unlock = format_unlock_notification(user.id, user.first_name, user.username, achievements)
    if unlock:
        parts.append(unlock)
    final_text = "\n\n".join(parts)
    try:
        await spinning.edit_text(final_text)
    except Exception:  # noqa: BLE001
        # Редактирование не прошло (сообщение удалили и т.п.) — отдаём результат
        # отдельным сообщением, чтобы игрок всё равно увидел исход.
        await message.answer(final_text)



