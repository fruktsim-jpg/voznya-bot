"""Хендлеры рейтингов: /топ, /топнеделя и /семьи."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import top_pagination
from app.core.money import money
from app.core.utils import display_name, format_marriage_duration_days, place_marker

from app.models import User
from app.repositories import economy as economy_repo
from app.repositories import marriages as marriages_repo
from app.repositories import users as users_repo
from app.services.deletion import get_deletion_service
from app.settings import balance, texts

router = Router(name="ratings")

PAGE_SIZE = 10  # Игроков на страницу


async def render_top(session: AsyncSession, page: int, user_id: int | None) -> tuple[str, int]:
    """Формирует текст рейтинга богачей с пагинацией.
    
    Returns:
        (text, total_pages)
    """
    total_users = await users_repo.count_users_with_balance(session)
    total_pages = (total_users + PAGE_SIZE - 1) // PAGE_SIZE if total_users > 0 else 0
    
    if total_users == 0:
        return (texts.TOP_RICH_EMPTY, 0)
    
    # Ограничить страницу
    page = max(1, min(page, total_pages))
    
    top = await users_repo.top_by_balance_paginated(session, page, PAGE_SIZE)
    
    # Формируем строки
    offset = (page - 1) * PAGE_SIZE
    rows = "\n".join(
        texts.TOP_RICH_ROW.format(
            place=place_marker(offset + i + 1),
            mention=display_name(u.first_name, u.username),
            balance=money(u.balance),

        )
        for i, u in enumerate(top)
    )
    
    parts = [texts.TOP_RICH_HEADER.format(rows=rows)]
    
    # Место текущего игрока (только для владельца)
    if user_id:
        rank = await users_repo.get_user_rank_by_balance(session, user_id)
        if rank:
            parts.append(texts.TOP_RICH_USER_RANK.format(rank=rank))
    
    # Пагинация (если больше 1 страницы)
    if total_pages > 1:
        parts.append(texts.TOP_RICH_PAGE.format(page=page, total=total_pages))
    
    return ("\n\n".join(parts), total_pages)


@router.message(RuCommand("топ", "top", "рейтинг", "лидеры", "богачи", "богатые"))

async def cmd_top(message: Message, session: AsyncSession, command_args: str) -> None:
    """Рейтинг богатства: /топ."""
    sender = message.from_user
    user_id = sender.id if sender else None
    
    text, total_pages = await render_top(session, page=1, user_id=user_id)
    
    deletion = get_deletion_service()
    
    # Добавить кнопки только если больше 1 страницы
    if total_pages > 1 and user_id:
        sent = await message.answer(text, reply_markup=top_pagination(1, total_pages, user_id))
    else:
        sent = await message.answer(text)
    
    # Автоудаление информационного сообщения
    if user_id:
        await deletion.schedule_info_message(
            session,
            user_id=user_id,
            chat_id=message.chat.id,
            user_command_id=message.message_id,
            bot_message_id=sent.message_id,
        )


@router.callback_query(F.data.startswith("top:page:"))
async def cb_top_page(callback: CallbackQuery, session: AsyncSession) -> None:
    """Переключение страниц топа."""
    parts = callback.data.split(":")
    page = int(parts[2])
    user_id = int(parts[3])
    
    # Защита: только свой топ
    if callback.from_user and callback.from_user.id != user_id:
        await callback.answer(texts.CB_NOT_YOURS, show_alert=True)
        return
    
    text, total_pages = await render_top(session, page, user_id)
    
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=top_pagination(page, total_pages, user_id) if total_pages > 1 else None
        )
    await callback.answer()


@router.message(RuCommand("топнеделя", "weekly"))
async def cmd_weekly(message: Message, session: AsyncSession, command_args: str) -> None:
    """Топ по заработку за неделю: /топнеделя."""
    sender = message.from_user
    user_id = sender.id if sender else None
    
    top = await economy_repo.weekly_top_earners(
        session, balance.WEEKLY_DAYS, balance.TOP_WEEKLY_LIMIT
    )
    
    deletion = get_deletion_service()
    
    if not top:
        await message.answer(texts.WEEKLY_EMPTY)
        return

    rows = "\n".join(
        texts.WEEKLY_ROW.format(
            place=place_marker(i + 1),
            mention=display_name(u.first_name, u.username),
            amount=money(earned),

        )
        for i, (u, earned) in enumerate(top)
    )
    sent = await message.answer(texts.WEEKLY_HEADER.format(rows=rows))
    
    # Автоудаление информационного сообщения
    if user_id:
        await deletion.schedule_info_message(
            session,
            user_id=user_id,
            chat_id=message.chat.id,
            user_command_id=message.message_id,
            bot_message_id=sent.message_id,
        )


@router.message(RuCommand("семьи", "families", "браки", "свадьбы"))

async def cmd_families(message: Message, session: AsyncSession, command_args: str) -> None:
    """Рейтинг самых долгих семей: /семьи."""
    sender = message.from_user
    user_id = sender.id if sender else None
    
    marriages = await marriages_repo.top_longest_marriages(
        session, balance.TOP_FAMILIES_LIMIT
    )
    
    deletion = get_deletion_service()
    
    if not marriages:
        await message.answer(texts.TOP_FAMILIES_EMPTY)
        return

    lines: list[str] = []
    for i, m in enumerate(marriages):
        u1 = await session.get(User, m.user_id_1)
        u2 = await session.get(User, m.user_id_2)
        lines.append(
            texts.TOP_FAMILIES_ROW.format(
                place=place_marker(i + 1),
                first=display_name(u1.first_name, u1.username) if u1 else "?",
                second=display_name(u2.first_name, u2.username) if u2 else "?",

                duration=format_marriage_duration_days(m.married_at),
            )
        )
    sent = await message.answer(texts.TOP_FAMILIES_HEADER.format(rows="\n".join(lines)))
    
    # Автоудаление информационного сообщения
    if user_id:
        await deletion.schedule_info_message(
            session,
            user_id=user_id,
            chat_id=message.chat.id,
            user_command_id=message.message_id,
            bot_message_id=sent.message_id,
        )
