"""Хендлеры системы рейтинга MMR.

Команды:

* «ммр» / «mmr» / «рейтинг» — карточка рейтинга игрока (MMR + ранг);
* «топммр» / «topmmr» — топ сообщества по рейтингу.

Игрок видит ТОЛЬКО «MMR» и ранг — никакого XP в интерфейсе.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.utils import mention, place_marker
from app.repositories import mmr as mmr_repo
from app.settings import mmr as mmr_texts

router = Router(name="mmr")


@router.message(RuCommand("ммр", "mmr", "рейтинг"))
async def cmd_mmr(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Показывает карточку рейтинга: 🏆 MMR и 🎖 Ранг.

    Без аргументов — свой рейтинг; в ответ на сообщение — рейтинг автора.
    """
    user = message.from_user
    if user is None:
        return

    target_id = user.id
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None:
        target_id = reply.from_user.id

    mmr = await mmr_repo.get_mmr(session, target_id)
    rank = mmr_texts.get_rank(mmr)
    await message.answer(
        mmr_texts.MMR_CARD.format(
            mmr=mmr, rank_emoji=rank.emoji, rank_name=rank.name
        )
    )


@router.message(RuCommand("топммр", "topmmr"))
async def cmd_top_mmr(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Показывает топ игроков по рейтингу."""
    top = await mmr_repo.top_by_mmr(session, mmr_texts.TOP_MMR_LIMIT)
    if not top:
        await message.answer(mmr_texts.MMR_TOP_EMPTY)
        return

    rows = "\n".join(
        mmr_texts.MMR_TOP_ROW.format(
            place=place_marker(i + 1),
            mention=mention(row.user_id, row.first_name, row.username),
            mmr=row.mmr,
        )
        for i, row in enumerate(top)
    )
    await message.answer(mmr_texts.MMR_TOP_HEADER.format(rows=rows))
