"""Обработчики команды /profile — показ профиля игрока."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.models import User
from app.settings.titles import get_title, get_next_title

router = Router(name="profile")


async def render_profile(session: AsyncSession, user: User) -> str:
    """Формирует текст профиля игрока."""
    from app.features.achievements.service import get_unlocked_codes
    from app.repositories.marriages import get_active_marriage
    from app.repositories.users import get_user
    from app.settings.achievements import ACHIEVEMENTS
    
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
    
    # Достижения
    unlocked = await get_unlocked_codes(session, user.user_id)
    total_achievements = len(ACHIEVEMENTS)
    opened_achievements = len(unlocked)
    text += f"🏅 Достижения: {opened_achievements}/{total_achievements}\n"
    
    # Брак
    marriage = await get_active_marriage(session, user.user_id)
    if marriage:
        partner_id = marriage.user_id_2 if marriage.user_id_1 == user.user_id else marriage.user_id_1
        partner = await get_user(session, partner_id)
        if partner:
            partner_name = partner.display_name()
            text += f"💍 В браке с {partner_name}\n"
    
    text += (
        f"\n⚔️ Дуэли: {user.duels_won} побед / {user.duels_lost} поражений\n"
        f"🌾 Серия фермы: {user.farm_streak} (рекорд: {user.max_farm_streak})\n"
        f"📦 Кладов найдено: {user.treasures_found}\n\n"
        f"Полная статистика на сайте: {settings.website_url}/profile/{user.user_id}"
    )
    
    return text


@router.message(RuCommand("профиль", "profile"))
async def profile_command(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает профиль игрока с кнопкой на сайт."""
    
    from app.repositories.users import get_user
    
    user_tg = message.from_user
    if user_tg is None:
        return
    
    user = await get_user(session, user_tg.id)
    if user is None:
        await message.answer("❌ Пользователь не найден в базе данных.")
        return
    
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
