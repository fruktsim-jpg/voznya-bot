"""Хендлеры команды /профиль (карточка игрока)."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.keyboards import quick_actions
from app.core.money import money
from app.core.targets import resolve_target
from app.core.utils import format_marriage_duration, mention
from app.features.achievements.service import get_unlocked_codes
from app.models import User
from app.repositories import marriages as marriages_repo
from app.repositories import users as users_repo
from app.services.deletion import get_deletion_service
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


def _duels_block(user: User) -> str:
    """Формирует строку дуэлей."""
    total = user.duels_won + user.duels_lost
    if total == 0:
        return texts.PROFILE_DUELS_NONE
    return texts.PROFILE_DUELS_COMPACT.format(wins=user.duels_won, losses=user.duels_lost)


def _streak_block(user: User) -> str:
    """Формирует строку серии фермы."""
    if user.farm_streak == 0:
        return ""  # Скрываем если нет серии
    return texts.PROFILE_STREAK.format(days=user.farm_streak)


def _progress_block(earned: int) -> str:
    """Формирует короткую строку прогресса до следующего титула (без бара)."""
    next_title = get_next_title(earned)
    if next_title is None:
        return texts.PROFILE_PROGRESS_MAX
    remaining = next_title.min_earned - earned
    return texts.PROFILE_PROGRESS.format(
        next_title=next_title.name,
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
        duels=_duels_block(user),
        treasures=user.treasures_found,
        ach_opened=len(unlocked),
        ach_total=len(ACHIEVEMENTS),
        streak=_streak_block(user),
        marital=marital,
        progress=_progress_block(user.total_earned),
    )


@router.message(RuCommand("профиль", "profile", "проф", "стата"))
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

    deletion = get_deletion_service()
    
    # Удаляем команду пользователя через 5 сек
    await deletion.schedule(session, message.chat.id, message.message_id, 5)
    
    # Кнопка на сайт (только для своего профиля)
    keyboard = quick_actions()
    if user.user_id == sender.id:
        settings = get_settings()
        profile_url = f"{settings.website_url}/profile/{user.user_id}"
        website_button = InlineKeyboardButton(text="🌐 Открыть на сайте", url=profile_url)
        if keyboard.inline_keyboard:
            keyboard.inline_keyboard.append([website_button])
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[website_button]])
    
    # Отправляем профиль и удаляем через 5 минут
    sent = await message.answer(await render_profile(session, user), reply_markup=keyboard)
    await deletion.schedule(session, sent.chat.id, sent.message_id, 300)
