"""Обработчики быстрых кнопок (💊 Ферма, 💰 Баланс, 👤 Профиль, 🏆 Топ).

Каждое нажатие выполняет действие для нажавшего и отправляет новый ответ,
чтобы кнопки могли использовать разные пользователи независимо.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.keyboards import quick_actions
from app.core.money import money
from app.core.utils import format_cooldown, mention
from app.features.achievements.service import check_award_and_notify
from app.features.farm.handlers import render_farm_result
from app.features.farm.service import do_farm
from app.features.profile.handlers import render_profile
from app.features.ratings.handlers import render_top
from app.repositories import users as users_repo
from app.services.economy import get_balance
from app.settings import texts

router = Router(name="quick")


@router.callback_query(F.data == "quick:farm")
async def q_farm(callback: CallbackQuery, session: AsyncSession) -> None:
    """Быстрая ферма."""
    user = callback.from_user
    result = await do_farm(session, user.id)
    if result.on_cooldown:
        await callback.answer(
            texts.COOLDOWN_NOTICE.format(time=format_cooldown(result.remaining)),
            show_alert=True,
        )
        return
    who = mention(user.id, user.first_name, user.username)
    if callback.message is not None:
        await callback.message.answer(
            render_farm_result(result, who), reply_markup=quick_actions()
        )
        await check_award_and_notify(
            callback.message, session, user.id, user.first_name, user.username
        )
    await callback.answer()


@router.callback_query(F.data == "quick:balance")
async def q_balance(callback: CallbackQuery, session: AsyncSession) -> None:
    """Быстрый баланс."""
    user = callback.from_user
    amount = await get_balance(session, user.id)
    if callback.message is not None:
        await callback.message.answer(
            texts.BALANCE.format(
                mention=mention(user.id, user.first_name, user.username),
                balance=money(amount),
            )
        )
    await callback.answer()


@router.callback_query(F.data == "quick:profile")
async def q_profile(callback: CallbackQuery, session: AsyncSession) -> None:
    """Быстрый профиль."""
    user = callback.from_user
    record = await users_repo.get_user(session, user.id)
    if record is not None and callback.message is not None:
        await callback.message.answer(
            await render_profile(session, record), reply_markup=quick_actions()
        )
    await callback.answer()


@router.callback_query(F.data == "quick:top")
async def q_top(callback: CallbackQuery, session: AsyncSession) -> None:
    """Быстрый топ."""
    if callback.message is not None:
        await callback.message.answer(await render_top(session))
    await callback.answer()
