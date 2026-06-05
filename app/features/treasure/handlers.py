"""Хендлеры клада: команда /снять и кнопка «📦 Забрать клад»."""

from __future__ import annotations

import random

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.money import money
from app.core.utils import mention
from app.features.achievements.service import check_award_and_notify, notify_specific
from app.features.treasure.service import claim_treasure
from app.settings import texts

router = Router(name="treasure")


async def _do_claim(
    answerable,
    session: AsyncSession,
    user_id: int,
    first_name: str | None,
    username: str | None,
    chat_id: int,
) -> bool:
    """Общая логика взятия клада. Возвращает True, если клад забран.

    ``answerable`` — объект с методом ``answer`` (Message или
    callback.message), куда уйдёт сообщение о результате.
    """
    result = await claim_treasure(session, user_id, chat_id)
    if result.status == "none":
        return False

    await answerable.answer(
        random.choice(texts.TREASURE_CLAIM_VARIANTS).format(
            mention=mention(user_id, first_name, username),
            reward=money(result.reward),
        )
    )
    await check_award_and_notify(answerable, session, user_id, first_name, username)
    if result.fast:
        await notify_specific(answerable, session, user_id, first_name, username, "kladmen")
    return True


@router.message(RuCommand("снять", "claim", "клад", "забрать", "открыть"))
async def cmd_claim(message: Message, session: AsyncSession, command_args: str) -> None:
    """Обрабатывает команду /снять."""
    user = message.from_user
    if user is None:
        return

    claimed = await _do_claim(
        message, session, user.id, user.first_name, user.username, message.chat.id
    )
    if not claimed:
        await message.answer(texts.TREASURE_NONE)


@router.callback_query(F.data == "treasure:claim")
async def cb_claim(callback: CallbackQuery, session: AsyncSession) -> None:
    """Забирает клад по нажатию кнопки «📦 Забрать клад» (первый успевший)."""
    user = callback.from_user
    if user is None or callback.message is None:
        await callback.answer()
        return

    claimed = await _do_claim(
        callback.message,
        session,
        user.id,
        user.first_name,
        user.username,
        callback.message.chat.id,
    )
    if claimed:
        # Клад забран — убираем кнопку, чтобы остальные не жали впустую.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        await callback.answer()
    else:
        await callback.answer(texts.TREASURE_NONE, show_alert=True)
