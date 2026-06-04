"""Административные команды: /выдать, /забрать, /инфо, /клад.

Доступны только пользователям из ADMIN_IDS.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db import get_sessionmaker
from app.core.filters import RuCommand
from app.core.money import money
from app.core.targets import extract_amount_after_target, resolve_target
from app.core.utils import mention
from app.features.achievements.service import check_award_and_notify
from app.features.treasure.service import spawn_treasure
from app.services.economy import change_balance
from app.settings import balance, texts

router = Router(name="admin")


def _is_admin(message: Message) -> bool:
    return message.from_user is not None and get_settings().is_admin(message.from_user.id)


async def _parse_target_amount(
    session: AsyncSession, message: Message, command_args: str
):
    """Возвращает (target_user, amount) или (None, None) при ошибке разбора.

    Сумма должна быть положительной и не превышать ADMIN_MAX_AMOUNT.
    """
    target = await resolve_target(session, message, command_args)
    amount_str = extract_amount_after_target(command_args)
    if (
        target is None
        or not amount_str
        or len(amount_str) > 12
        or not amount_str.lstrip("-").isdigit()
    ):
        return None, None
    amount = int(amount_str)
    if amount <= 0 or amount > balance.ADMIN_MAX_AMOUNT:
        return None, None
    return target, amount


@router.message(RuCommand("выдать", "give"))
async def cmd_give(message: Message, session: AsyncSession, command_args: str) -> None:
    """Начисляет ешки пользователю: /выдать @username сумма."""
    if not _is_admin(message):
        await message.answer(texts.ADMIN_ONLY)
        return

    target, amount = await _parse_target_amount(session, message, command_args)
    if target is None or amount is None:
        await message.answer(texts.ADMIN_GIVE_USAGE)
        return

    user = await change_balance(
        session, target.user_id, amount, "admin", {"action": "give"}
    )
    await message.answer(
        texts.ADMIN_GIVE_DONE.format(
            amount=money(amount),
            mention=mention(target.user_id, target.first_name, target.username),
            balance=money(user.balance),
        )
    )
    await check_award_and_notify(
        message, session, target.user_id, target.first_name, target.username
    )


@router.message(RuCommand("забрать", "take"))
async def cmd_take(message: Message, session: AsyncSession, command_args: str) -> None:
    """Списывает ешки у пользователя: /забрать @username сумма."""
    if not _is_admin(message):
        await message.answer(texts.ADMIN_ONLY)
        return

    target, amount = await _parse_target_amount(session, message, command_args)
    if target is None or amount is None:
        await message.answer(texts.ADMIN_TAKE_USAGE)
        return

    # Не уводим баланс в минус: списываем не больше, чем есть.
    take = min(amount, max(target.balance, 0))
    if take <= 0:
        await message.answer(
            texts.ADMIN_TAKE_DONE.format(
                amount=money(0),
                mention=mention(target.user_id, target.first_name, target.username),
                balance=money(target.balance),
            )
        )
        return

    user = await change_balance(
        session, target.user_id, -take, "admin", {"action": "take"}
    )
    await message.answer(
        texts.ADMIN_TAKE_DONE.format(
            amount=money(take),
            mention=mention(target.user_id, target.first_name, target.username),
            balance=money(user.balance),
        )
    )


@router.message(RuCommand("инфо", "info"))
async def cmd_info(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает подробную информацию о пользователе: /инфо @username."""
    if not _is_admin(message):
        await message.answer(texts.ADMIN_ONLY)
        return

    target = await resolve_target(session, message, command_args)
    if target is None:
        await message.answer(texts.ADMIN_INFO_USAGE)
        return

    await message.answer(
        texts.ADMIN_INFO.format(
            mention=mention(target.user_id, target.first_name, target.username),
            user_id=target.user_id,
            balance=money(target.balance),
            earned=money(target.total_earned),
            spent=money(target.total_spent),
            streak=target.farm_streak,
            max_streak=target.max_farm_streak,
            pidor=target.pidor_count,
            wins=target.duels_won,
            losses=target.duels_lost,
            treasures=target.treasures_found,
            farms=target.farm_success_count,
            casino=target.casino_games_count,
        )
    )


@router.message(RuCommand("клад", "spawntreasure"))
async def cmd_spawn_treasure(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Принудительно создаёт клад: /клад (только админ)."""
    if not _is_admin(message):
        await message.answer(texts.ADMIN_ONLY)
        return

    settings = get_settings()
    assert message.bot is not None
    # Спавн использует собственную сессию, поэтому фиксируем текущую заранее.
    await session.commit()
    await spawn_treasure(message.bot, get_sessionmaker(), settings.chat_id)
    await message.answer(texts.ADMIN_TREASURE_DONE)
