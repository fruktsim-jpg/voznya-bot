"""Хендлеры рейтингов: /топ и /семьи."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.utils import format_marriage_duration, mention
from app.models import User
from app.repositories import marriages as marriages_repo
from app.repositories import users as users_repo
from app.settings import balance, texts

router = Router(name="ratings")


@router.message(RuCommand("топ", "top"))
async def cmd_top(message: Message, session: AsyncSession, command_args: str) -> None:
    """Рейтинг богатства: /топ."""
    top = await users_repo.top_by_balance(session, balance.TOP_RICH_LIMIT)
    if not top:
        await message.answer(texts.TOP_RICH_EMPTY.format(currency=balance.CURRENCY_NAME))
        return

    rows = "\n".join(
        texts.TOP_RICH_ROW.format(
            place=i + 1,
            mention=mention(u.user_id, u.first_name, u.username),
            balance=u.balance,
            currency=balance.CURRENCY_NAME,
        )
        for i, u in enumerate(top)
    )
    await message.answer(texts.TOP_RICH_HEADER.format(rows=rows))


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
                place=i + 1,
                first=mention(u1.user_id, u1.first_name, u1.username) if u1 else "?",
                second=mention(u2.user_id, u2.first_name, u2.username) if u2 else "?",
                duration=format_marriage_duration(m.married_at),
            )
        )
    await message.answer(texts.TOP_FAMILIES_HEADER.format(rows="\n".join(lines)))
