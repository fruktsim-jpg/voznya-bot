"""Хендлеры команды /снять (забрать клад)."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.utils import mention
from app.features.treasure.service import claim_treasure
from app.settings import balance, texts

router = Router(name="treasure")


@router.message(RuCommand("снять"))
async def cmd_claim(message: Message, session: AsyncSession, command_args: str) -> None:
    """Обрабатывает команду /снять."""
    user = message.from_user
    if user is None:
        return

    result = await claim_treasure(session, user.id, message.chat.id)

    if result.status == "none":
        await message.answer(texts.TREASURE_NONE)
        return

    await message.answer(
        texts.TREASURE_CLAIMED.format(
            mention=mention(user.id, user.first_name, user.username),
            reward=result.reward,
            currency=balance.CURRENCY_NAME,
        )
    )
