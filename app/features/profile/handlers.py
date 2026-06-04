"""Хендлеры команды /профиль."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.targets import resolve_target
from app.core.utils import format_marriage_duration, mention
from app.models import User
from app.repositories import marriages as marriages_repo
from app.repositories import users as users_repo
from app.settings import balance, texts

router = Router(name="profile")


@router.message(RuCommand("профиль", "profile"))
async def cmd_profile(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает профиль пользователя (свой или указанного)."""
    sender = message.from_user
    if sender is None:
        return

    # Можно посмотреть чужой профиль (reply / @username), по умолчанию — свой.
    target = await resolve_target(session, message, command_args)
    user: User | None = target or await users_repo.get_user(session, sender.id)
    if user is None:
        await message.answer(texts.USER_NOT_FOUND)
        return

    marriage = await marriages_repo.get_active_marriage(session, user.user_id)
    if marriage is None:
        marital = texts.PROFILE_SINGLE
    else:
        partner_id = (
            marriage.user_id_2
            if marriage.user_id_1 == user.user_id
            else marriage.user_id_1
        )
        partner = await users_repo.get_user(session, partner_id)
        partner_mention = (
            mention(partner.user_id, partner.first_name, partner.username)
            if partner
            else "кто-то"
        )
        marital = texts.PROFILE_MARRIED.format(
            partner=partner_mention,
            duration=format_marriage_duration(marriage.married_at),
        )

    await message.answer(
        texts.PROFILE.format(
            mention=mention(user.user_id, user.first_name, user.username),
            balance=user.balance,
            currency=balance.CURRENCY_NAME,
            streak=user.farm_streak,
            max_streak=user.max_farm_streak,
            pidor_count=user.pidor_count,
            wins=user.duels_won,
            losses=user.duels_lost,
            treasures=user.treasures_found,
            marital=marital,
        )
    )
