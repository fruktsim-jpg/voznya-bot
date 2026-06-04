"""Хендлеры браков: /жениться, /да, /брак, /развод, /подтвердить."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.targets import resolve_target
from app.core.utils import format_marriage_duration, mention
from app.features.marriage import service
from app.models import User
from app.settings import balance, texts

router = Router(name="marriage")


async def _mention_of(session: AsyncSession, user_id: int) -> str:
    user = await session.get(User, user_id)
    if user is None:
        return "кто-то"
    return mention(user.user_id, user.first_name, user.username)


@router.message(RuCommand("жениться", "marry"))
async def cmd_marry(message: Message, session: AsyncSession, command_args: str) -> None:
    """Предложение руки и сердца: /жениться @username."""
    user = message.from_user
    if user is None:
        return

    target = await resolve_target(session, message, command_args)
    if target is None:
        await message.answer(texts.MARRY_USAGE)
        return
    if target.user_id == user.id:
        await message.answer(texts.MARRY_SELF)
        return

    result = await service.propose(session, user.id, target.user_id, message.chat.id)

    if result.status == "initiator_busy":
        await message.answer(texts.MARRY_INITIATOR_BUSY)
        return
    if result.status == "target_busy":
        await message.answer(
            texts.MARRY_TARGET_BUSY.format(
                mention=mention(target.user_id, target.first_name, target.username)
            )
        )
        return

    await message.answer(
        texts.MARRY_PROPOSAL.format(
            initiator=mention(user.id, user.first_name, user.username),
            target=mention(target.user_id, target.first_name, target.username),
            minutes=balance.MARRIAGE_PROPOSAL_EXPIRE_MINUTES,
        )
    )


@router.message(RuCommand("да", "yes"))
async def cmd_accept(message: Message, session: AsyncSession, command_args: str) -> None:
    """Согласие на брак: /да."""
    user = message.from_user
    if user is None:
        return

    result = await service.accept_proposal(session, user.id)

    if result.status == "no_pending":
        await message.answer(texts.MARRY_NO_PENDING)
        return
    if result.status in {"initiator_busy", "target_busy"}:
        await message.answer(texts.MARRY_INITIATOR_BUSY)
        return

    await message.answer(
        texts.MARRY_DONE.format(
            first=await _mention_of(session, result.initiator_id),
            second=await _mention_of(session, result.target_id),
        )
    )


@router.message(RuCommand("брак", "marriage"))
async def cmd_marriage_info(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Информация о браке: /брак."""
    user = message.from_user
    if user is None:
        return

    marriage = await service.get_marriage(session, user.id)
    if marriage is None:
        await message.answer(
            texts.MARRIAGE_NONE.format(
                mention=mention(user.id, user.first_name, user.username)
            )
        )
        return

    await message.answer(
        texts.MARRIAGE_INFO.format(
            first=await _mention_of(session, marriage.user_id_1),
            second=await _mention_of(session, marriage.user_id_2),
            duration=format_marriage_duration(marriage.married_at),
        )
    )


@router.message(RuCommand("развод", "divorce"))
async def cmd_divorce(message: Message, session: AsyncSession, command_args: str) -> None:
    """Запрос на развод: /развод."""
    user = message.from_user
    if user is None:
        return

    result = await service.request_divorce(session, user.id, message.chat.id)
    if result.status == "no_marriage":
        await message.answer(
            texts.DIVORCE_NO_MARRIAGE.format(
                mention=mention(user.id, user.first_name, user.username)
            )
        )
        return

    await message.answer(
        texts.DIVORCE_REQUEST.format(
            initiator=mention(user.id, user.first_name, user.username),
            target=await _mention_of(session, result.partner_id),
            minutes=balance.MARRIAGE_PROPOSAL_EXPIRE_MINUTES,
        )
    )


@router.message(RuCommand("подтвердить", "confirm"))
async def cmd_confirm_divorce(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Подтверждение развода: /подтвердить."""
    user = message.from_user
    if user is None:
        return

    result = await service.confirm_divorce(session, user.id)
    if result.status == "no_pending":
        await message.answer(texts.DIVORCE_NO_PENDING)
        return

    await message.answer(
        texts.DIVORCE_DONE.format(
            first=await _mention_of(session, result.initiator_id),
            second=await _mention_of(session, result.target_id),
        )
    )
