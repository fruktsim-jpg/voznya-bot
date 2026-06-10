"""Хендлеры рейтингов: /топ, /топнеделя и /семьи."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.keyboards import open_on_site, supports_web_app, top_pagination
from app.core.money import money
from app.core.responses import notify_and_cleanup

from app.core.utils import display_name, format_marriage_duration_days, place_marker

from app.models import User
from app.repositories import economy as economy_repo
from app.repositories import marriages as marriages_repo
from app.repositories import users as users_repo
from app.services.deletion import get_deletion_service
from app.settings import balance, texts

router = Router(name="ratings")

PAGE_SIZE = 10  # Игроков на страницу


async def render_top(session: AsyncSession, page: int) -> tuple[str, int]:
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
    
    # Пагинация (если больше 1 страницы)
    if total_pages > 1:
        parts.append(texts.TOP_RICH_PAGE.format(page=page, total=total_pages))
    
    return ("\n\n".join(parts), total_pages)


@router.message(RuCommand("топ", "top", "рейтинг", "лидеры", "богачи", "богатые"))

async def cmd_top(message: Message, session: AsyncSession, command_args: str) -> None:
    """Рейтинг богатства: /топ."""
    text, total_pages = await render_top(session, page=1)
    deletion = get_deletion_service()

    markup = top_pagination(1, total_pages) if total_pages > 1 else None
    sent = await message.answer(text, reply_markup=markup)
    await deletion.replace_leaderboard_message(
        message.chat.id,
        "balance",
        message.message_id,
        sent.message_id,
    )
    await deletion.schedule(session, message.chat.id, message.message_id, 1)


@router.callback_query(F.data.startswith("top:page:"))
async def cb_top_page(callback: CallbackQuery, session: AsyncSession) -> None:
    """Переключение страниц топа."""
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    page = int(parts[2])
    text, total_pages = await render_top(session, page)
    
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=top_pagination(page, total_pages) if total_pages > 1 else None
        )
    await callback.answer()


@router.message(RuCommand("топнеделя", "weekly"))
async def cmd_weekly(message: Message, session: AsyncSession, command_args: str) -> None:
    """Топ по заработку за неделю: /топнеделя."""
    top = await economy_repo.weekly_top_earners(
        session, balance.WEEKLY_DAYS, balance.TOP_WEEKLY_LIMIT
    )
    
    deletion = get_deletion_service()
    
    if not top:
        await notify_and_cleanup(session, message, texts.WEEKLY_EMPTY)
        return

    rows = "\n".join(
        texts.WEEKLY_ROW.format(
            place=place_marker(i + 1),
            mention=display_name(u.first_name, u.username),
            amount=money(earned),

        )
        for i, (u, earned) in enumerate(top)
    )
    # Site-first: CTA на полную статистику сайта (рейтинги/аналитика глубже).
    stats_url = f"{get_settings().website_url}/live"
    sent = await message.answer(
        texts.WEEKLY_HEADER.format(rows=rows),
        reply_markup=open_on_site(
            texts.TOP_STATS_SITE_BTN,
            stats_url,
            prefer_web_app=supports_web_app(message.chat.type),
        ),
    )
    await deletion.replace_leaderboard_message(
        message.chat.id,
        "weekly",
        message.message_id,
        sent.message_id,
    )
    await deletion.schedule(session, message.chat.id, message.message_id, 1)


@router.message(RuCommand("семьи", "families", "браки", "свадьбы"))

async def cmd_families(message: Message, session: AsyncSession, command_args: str) -> None:
    """Рейтинг самых долгих семей: /семьи."""
    marriages = await marriages_repo.top_longest_marriages(
        session, balance.TOP_FAMILIES_LIMIT
    )
    
    deletion = get_deletion_service()
    
    if not marriages:
        await notify_and_cleanup(session, message, texts.TOP_FAMILIES_EMPTY)
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
    families_url = f"{get_settings().website_url}/live"
    sent = await message.answer(
        texts.TOP_FAMILIES_HEADER.format(rows="\n".join(lines)),
        reply_markup=open_on_site(
            texts.TOP_STATS_SITE_BTN,
            families_url,
            prefer_web_app=supports_web_app(message.chat.type),
        ),
    )
    await deletion.replace_leaderboard_message(
        message.chat.id,
        "families",
        message.message_id,
        sent.message_id,
    )
    await deletion.schedule(session, message.chat.id, message.message_id, 1)
