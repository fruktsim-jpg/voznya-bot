"""Хендлеры браков: /жениться, /да, /брак, /развод, /подтвердить + кнопка."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import marriage_accept, quick_actions
from app.core.targets import resolve_target
from app.core.utils import format_marriage_duration, mention
from app.features.achievements.service import check_award_and_notify
from app.features.marriage import service
from app.models import User
from app.settings import balance, texts

router = Router(name="marriage")


async def _mention_of(session: AsyncSession, user_id: int) -> str:
    user = await session.get(User, user_id)
    if user is None:
        return "кто-то"
    return mention(user.user_id, user.first_name, user.username)


async def _finish_marriage(
    answerable, session: AsyncSession, initiator_id: int, target_id: int
) -> None:
    """Объявляет о браке и проверяет достижения у обоих супругов."""
    await answerable.answer(
        texts.MARRY_DONE.format(
            first=await _mention_of(session, initiator_id),
            second=await _mention_of(session, target_id),
        ),
        reply_markup=quick_actions(),
    )
    for uid in (initiator_id, target_id):
        u = await session.get(User, uid)
        if u is not None:
            await check_award_and_notify(answerable, session, u.user_id, u.first_name, u.username)


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
        ),
        reply_markup=marriage_accept(result.pending_id),
    )


@router.message(RuCommand("да", "yes"))
async def cmd_accept(message: Message, session: AsyncSession, command_args: str) -> None:
    """Согласие на брак: /да."""
    user = message.from_user
    if user is None:
        return

    result = await service.accept_proposal(session, user.id)

    # При отсутствии предложения молчим (бытовое «да» не должно спамить чат).
    if result.status == "no_pending":
        return
    if result.status in {"initiator_busy", "target_busy"}:
        await message.answer(texts.MARRY_INITIATOR_BUSY)
        return

    await _finish_marriage(message, session, result.initiator_id, result.target_id)


@router.callback_query(F.data.startswith("marry:accept:"))
async def cb_marry_accept(callback: CallbackQuery, session: AsyncSession) -> None:
    """Согласие на брак кнопкой."""
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    pending_id = int(parts[2])

    result = await service.accept_proposal(session, callback.from_user.id, pending_id=pending_id)

    if result.status == "no_pending":
        await callback.answer(texts.CB_EXPIRED, show_alert=True)
        return
    if result.status == "not_target":
        await callback.answer(texts.CB_NOT_YOURS, show_alert=True)
        return
    if result.status in {"initiator_busy", "target_busy"}:
        await callback.answer(texts.MARRY_INITIATOR_BUSY, show_alert=True)
        return

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        await _finish_marriage(
            callback.message, session, result.initiator_id, result.target_id
        )
    await callback.answer()


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
    # При отсутствии запроса молчим.
    if result.status == "no_pending":
        return

    await message.answer(
        texts.DIVORCE_DONE.format(
            first=await _mention_of(session, result.initiator_id),
            second=await _mention_of(session, result.target_id),
        )
    )
