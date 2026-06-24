"""Хендлеры команды /ачивки (достижения)."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.keyboards import open_on_site, supports_web_app
from app.core.responses import send_info_window
from app.core.targets import resolve_target
from app.features.achievements.service import render_achievements_compact
from app.repositories import users as users_repo


router = Router(name="achievements")


@router.message(RuCommand("ачивки", "achievements", "ачивы", "достижения"))
async def cmd_achievements(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Показывает достижения пользователя (свои или указанного)."""
    sender = message.from_user
    if sender is None:
        return

    target = await resolve_target(session, message, command_args)
    user = target or await users_repo.get_user(session, sender.id)
    user_id = user.user_id if user else sender.id
    
    # Определяем имя и username для отображения
    if target:
        # Показываем чужие достижения
        first_name = target.first_name or "Пользователь"
        username = target.username
    else:
        # Показываем свои достижения
        first_name = sender.first_name
        username = sender.username

    # Отправляем компактные ачивки (без кнопки)
    await send_info_window(
        session,
        message,
        "achievements",
        await render_achievements_compact(session, user_id, first_name, username),
        reply_markup=open_on_site(
            "🏅 Полный прогресс на сайте",
            f"{get_settings().website_url}/profile/{user_id}",
            prefer_web_app=supports_web_app(message.chat.type),
        ),
    )


