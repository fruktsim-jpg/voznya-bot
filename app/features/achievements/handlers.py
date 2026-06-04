"""Хендлеры команды /ачивки (достижения)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import achievements_full_button
from app.core.targets import resolve_target
from app.features.achievements.service import render_achievements_compact, render_achievements_full
from app.repositories import users as users_repo
from app.services.deletion import get_deletion_service
from app.settings import texts

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

    deletion = get_deletion_service()
    
    # Удаляем команду пользователя через 5 сек
    await deletion.schedule(session, message.chat.id, message.message_id, 5)
    
    # Отправляем компактные ачивки с кнопкой и удаляем через 5 минут
    sent = await message.answer(
        await render_achievements_compact(session, user_id, first_name, username),
        reply_markup=achievements_full_button(user_id)
    )
    await deletion.schedule(session, sent.chat.id, sent.message_id, 300)


@router.callback_query(F.data.startswith("ach:full:"))
async def cb_achievements_full(callback: CallbackQuery, session: AsyncSession) -> None:
    """Показывает полный список достижений с категориями и замками."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    
    user_id = int(parts[2])
    
    # Защита: только свои достижения
    if callback.from_user and callback.from_user.id != user_id:
        await callback.answer(texts.CB_NOT_YOURS, show_alert=True)
        return
    
    # Получаем имя и username из callback.from_user
    first_name = callback.from_user.first_name if callback.from_user else "Пользователь"
    username = callback.from_user.username if callback.from_user else None
    
    text = await render_achievements_full(session, user_id, first_name, username)
    
    if callback.message:
        try:
            # Редактируем сообщение, убираем кнопку
            await callback.message.edit_text(text)
        except Exception:
            # Если не удалось — создаём новое
            await callback.message.answer(text)
    await callback.answer()
