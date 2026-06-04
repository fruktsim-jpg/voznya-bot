"""Хендлеры рейтингов: /топ, /топнеделя и /семьи."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.money import money
from app.core.utils import format_marriage_duration, mention, place_marker
from app.models import User
from app.repositories import economy as economy_repo
from app.repositories import marriages as marriages_repo
from app.repositories import users as users_repo
from app.settings import balance, texts

router = Router(name="ratings")


async def render_top(session: AsyncSession) -> str:
    """Формирует текст рейтинга богачей (используется командой и кнопкой)."""
    top = await users_repo.top_by_balance(session, balance.TOP_RICH_LIMIT)
    if not top:
        return texts.TOP_RICH_EMPTY
    rows = "\n".join(
        texts.TOP_RICH_ROW.format(
            place=place_marker(i + 1),
            mention=mention(u.user_id, u.first_name, u.username),
            balance=money(u.balance),
        )
        for i, u in enumerate(top)
    )
    return texts.TOP_RICH_HEADER.format(rows=rows)


@router.message(RuCommand("топ", "top"))
async def cmd_top(message: Message, session: AsyncSession, command_args: str) -> None:
    """Рейтинг богатства: /топ."""
    await message.answer(await render_top(session))


@router.message(RuCommand("топнеделя", "weekly"))
async def cmd_weekly(message: Message, session: AsyncSession, command_args: str) -> None:
    """Топ по заработку за неделю: /топнеделя."""
    top = await economy_repo.weekly_top_earners(
        session, balance.WEEKLY_DAYS, balance.TOP_WEEKLY_LIMIT
    )
    if not top:
        await message.answer(texts.WEEKLY_EMPTY)
        return

    rows = "\n".join(
        texts.WEEKLY_ROW.format(
            place=place_marker(i + 1),
            mention=mention(u.user_id, u.first_name, u.username),
            amount=money(earned),
        )
        for i, (u, earned) in enumerate(top)
    )
    await message.answer(texts.WEEKLY_HEADER.format(rows=rows))


@router.message(RuCommand("семьи", "families"))
async def cmd_families(message: Message, session: AsyncSession, command_args: str) -> None:
    """Рейтинг самых долгих семей: /семьи."""
    marriages = await marriages_repo.top_longest_marriages(
        session, balance.TOP_FAMILIES_LIMIT
    )
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
                first=mention(u1.user_id, u1.first_name, u1.username) if u1 else "?",
                second=mention(u2.user_id, u2.first_name, u2.username) if u2 else "?",
                duration=format_marriage_duration(m.married_at),
            )
        )
    await message.answer(texts.TOP_FAMILIES_HEADER.format(rows="\n".join(lines)))
