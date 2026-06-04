"""Обработчики команды /profile — показ профиля игрока."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.models import User
from app.settings.titles import get_title, get_next_title

router = Router(name="profile")


async def render_profile(session: AsyncSession, user: User) -> str:
    """Формирует текст профиля игрока."""
    settings = get_settings()
    title = get_title(user.total_earned)
    next_title = get_next_title(user.total_earned)
    
    text = (
        f"👤 <b>Профиль игрока</b>\n\n"
        f"💰 Баланс: <b>{user.balance:,}</b> ешек\n"
        f"📈 Заработано: <b>{user.total_earned:,}</b> ешек\n"
        f"🏆 Титул: {title.emoji} <b>{title.name}</b>\n"
    )
    
    # Прогресс до следующего титула
    if next_title:
        progress = user.total_earned - title.min_earned
        needed = next_title.min_earned - title.min_earned
        percent = int((progress / needed) * 100) if needed > 0 else 100
        text += f"📊 Прогресс: {percent}% до {next_title.emoji} {next_title.name}\n"
    
    text += (
        f"\n⚔️ Дуэли: {user.duels_won} побед / {user.duels_lost} поражений\n"
        f"🌾 Серия фермы: {user.farm_streak} (рекорд: {user.max_farm_streak})\n"
        f"📦 Кладов найдено: {user.treasures_found}\n\n"
        f"Полная статистика на сайте: {settings.website_url}/profile/{user.user_id}"
    )
    
    return text


@router.message(Command("profile"), RuCommand("профиль"))
async def profile_command(message: Message, user: User, session: AsyncSession) -> None:
    """Показывает профиль игрока с кнопкой на сайт."""
    settings = get_settings()
    text = await render_profile(session, user)
    
    # Кнопка на сайт
    profile_url = f"{settings.website_url}/profile/{user.user_id}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Открыть профиль на сайте", url=profile_url)]
        ]
    )
    
    await message.answer(text, reply_markup=keyboard)
