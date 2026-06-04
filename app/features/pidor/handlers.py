"""Хендлеры команды /пидор."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.money import money
from app.core.utils import mention
from app.features.pidor.service import get_or_choose_pidor
from app.models import User
from app.repositories import users as users_repo
from app.settings import balance, texts

router = Router(name="pidor")


async def _build_top(session: AsyncSession) -> str:
    """Формирует строку топ-10 по количеству статусов «Пидор дня»."""
    top = await users_repo.top_by_pidor(session, balance.TOP_PIDOR_LIMIT)
    if not top:
        return ""
    rows = "\n".join(
        texts.PIDOR_TOP_ROW.format(
            place=i + 1,
            mention=mention(u.user_id, u.first_name, u.username),
            count=u.pidor_count,
        )
        for i, u in enumerate(top)
    )
    return texts.PIDOR_TOP_HEADER.format(limit=balance.TOP_PIDOR_LIMIT, rows=rows)


@router.message(RuCommand("пидор", "pidor"))
async def cmd_pidor(message: Message, session: AsyncSession, command_args: str) -> None:
    """Обрабатывает команду /пидор."""
    user = message.from_user
    if user is None:
        return

    result = await get_or_choose_pidor(session, user.id)

    if result.status == "not_enough":
        await message.answer(
            texts.NOMINATION_NOT_ENOUGH.format(min=balance.NOMINATION_MIN_CANDIDATES)
        )
        return

    winner = await session.get(User, result.winner_id)
    who = (
        mention(winner.user_id, winner.first_name, winner.username)
        if winner
        else "кто-то"
    )
    top_block = await _build_top(session)

    if result.status == "chosen":
        text = texts.PIDOR_CHOSEN.format(
            mention=who,
            count=result.count,
            bonus=money(result.opener_bonus),
        )
    else:
        text = texts.PIDOR_TODAY.format(mention=who, count=result.count)

    await message.answer(text + top_block)
