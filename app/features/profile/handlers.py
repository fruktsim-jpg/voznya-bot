"""Хендлеры команды /профиль (карточка игрока)."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import quick_actions
from app.core.money import money
from app.core.targets import resolve_target
from app.core.utils import format_marriage_duration, mention
from app.features.achievements.service import get_unlocked_codes
from app.models import User
from app.repositories import marriages as marriages_repo
from app.repositories import users as users_repo
from app.settings import texts
from app.settings.achievements import ACHIEVEMENTS
from app.settings.titles import get_next_title, get_title

router = Router(name="profile")


async def _marital_status(session: AsyncSession, user_id: int) -> str:
    """Формирует строку семейного положения."""
    marriage = await marriages_repo.get_active_marriage(session, user_id)
    if marriage is None:
        return texts.PROFILE_SINGLE
    partner_id = (
        marriage.user_id_2 if marriage.user_id_1 == user_id else marriage.user_id_1
    )
    partner = await users_repo.get_user(session, partner_id)
    partner_mention = (
        mention(partner.user_id, partner.first_name, partner.username)
        if partner
        else "кто-то"
    )
    return texts.PROFILE_MARRIED.format(
        partner=partner_mention,
        duration=format_marriage_duration(marriage.married_at),
    )


def _progress_block(earned: int) -> str:
    """Формирует короткую строку прогресса до следующего титула (без бара)."""
    next_title = get_next_title(earned)
    if next_title is None:
        return texts.PROFILE_PROGRESS_MAX
    remaining = next_title.min_earned - earned
    return texts.PROFILE_PROGRESS.format(
        next_title=next_title.label,
        remaining=money(remaining),
    )


async def render_profile(session: AsyncSession, user: User) -> str:
    """Формирует текст карточки игрока (используется командой и кнопкой)."""
    title = get_title(user.total_earned)
    rank = await users_repo.rank_by_balance(session, user.balance)
    unlocked = await get_unlocked_codes(session, user.user_id)
    marital = await _marital_status(session, user.user_id)

    return texts.PROFILE.format(
        name=mention(user.user_id, user.first_name, user.username),
        title=title.label,
        rank=rank,
        balance=money(user.balance),
        wins=user.duels_won,
        losses=user.duels_lost,
        treasures=user.treasures_found,
        marital=marital,
        ach_opened=len(unlocked),
        ach_total=len(ACHIEVEMENTS),
        progress=_progress_block(user.total_earned),
    )


@router.message(RuCommand("профиль", "profile"))
async def cmd_profile(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает карточку игрока (свою или указанного пользователя)."""
    sender = message.from_user
    if sender is None:
        return

    target = await resolve_target(session, message, command_args)
    user: User | None = target or await users_repo.get_user(session, sender.id)
    if user is None:
        await message.answer(texts.USER_NOT_FOUND)
        return

    await message.answer(await render_profile(session, user), reply_markup=quick_actions())
