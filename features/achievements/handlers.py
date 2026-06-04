"""Обработчики команды /achievements — показ достижений игрока."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.models import User, UserAchievement
from app.settings.achievements import ACHIEVEMENTS

router = Router(name="achievements")


@router.message(Command("achievements"), RuCommand("ачивки", "достижения"))
async def achievements_command(message: Message, user: User, session: AsyncSession) -> None:
    """Показывает достижения игрока с кнопкой на сайт."""
    settings = get_settings()
    
    # Получаем достижения пользователя
    result = await session.execute(
        select(UserAchievement).where(UserAchievement.user_id == user.user_id)
    )
    user_achievements = result.scalars().all()
    unlocked_codes = {ach.code for ach in user_achievements}
    
    # Формируем текст
    text = f"🏆 <b>ДОСТИЖЕНИЯ</b>\n\n"
    text += f"Открыто: <b>{len(unlocked_codes)}</b> из <b>{len(ACHIEVEMENTS)}</b>\n\n"
    
    if unlocked_codes:
        for achievement in ACHIEVEMENTS:
            if achievement.code in unlocked_codes:
                text += f"{achievement.emoji} <b>{achievement.name}</b> (+{achievement.reward})\n"
    else:
        text += "У тебя пока нет достижений.\nИграй и открывай новые!"
    
    text += "\n📊 Все достижения и прогресс на сайте:"
    
    # Кнопка на сайт
    achievements_url = f"{settings.website_url}/live"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Все достижения", url=achievements_url)]
        ]
    )
    
    await message.answer(text, reply_markup=keyboard)
