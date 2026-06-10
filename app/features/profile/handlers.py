"""Обработчики команды /profile — показ профиля игрока."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.keyboards import profile_shortcuts, supports_web_app
from app.core.utils import display_name, format_marriage_duration_days
from app.models import User
from app.settings import texts
from app.settings.titles import get_title


router = Router(name="profile")


async def render_profile(session: AsyncSession, user: User) -> str:
    """Формирует компактный ежедневный профиль-хаб."""
    from app.features.achievements.service import get_unlocked_codes
    from app.repositories import marriages as marriages_repo
    from app.repositories import season as season_repo
    from app.settings.achievements import ACHIEVEMENTS

    title = get_title(user.total_earned)

    # MMR — отдельный игровой рейтинг (общий прогресс), не связан с ешками.
    from app.repositories.mmr import get_mmr
    from app.settings import mmr as mmr_settings

    mmr_value = await get_mmr(session, user.user_id)
    rank = mmr_settings.get_rank(mmr_value)

    lines = [
        f"👤 <b>Профиль — {user.display_name()}</b>\n\n"
        f"💰 Баланс: <b>{user.balance:,}</b> ешек",
        f"🏆 Титул: {title.emoji} <b>{title.name}</b>",
        f"🏅 MMR: <b>{mmr_value:,}</b> · {rank.emoji} <b>{rank.name}</b>",
    ]

    marriage = await marriages_repo.get_active_marriage(session, user.user_id)
    if marriage is None:
        lines.append("💍 Брак: нет")
    else:
        partner_id = marriage.user_id_2 if marriage.user_id_1 == user.user_id else marriage.user_id_1
        partner = await session.get(User, partner_id)
        partner_name = display_name(partner.first_name, partner.username) if partner else "партнёр"
        lines.append(
            f"💍 Брак: {partner_name} · {format_marriage_duration_days(marriage.married_at)}"
        )

    active_season = await season_repo.get_active_season(session)
    if active_season is not None:
        from app.settings import season as season_settings

        season_mmr = await season_repo.get_season_mmr(session, user.user_id)
        division = season_settings.get_division(season_mmr)
        lines.append(f"🗓 Сезон: {division.emoji} <b>{division.name}</b>")

    unlocked = await get_unlocked_codes(session, user.user_id)
    total_achievements = len(ACHIEVEMENTS)
    opened_achievements = len(unlocked)
    lines.append(f"🎖 Ачивки: <b>{opened_achievements}/{total_achievements}</b>")

    return "\n".join(lines)



async def _send_profile(message: Message, session: AsyncSession, user: User) -> None:
    """Отправляет карточку профиля игрока с кнопкой на сайт + автоудаление."""
    from app.services.deletion import get_deletion_service

    settings = get_settings()
    text = await render_profile(session, user)

    sent = await message.answer(
        text,
        reply_markup=profile_shortcuts(
            settings.website_url,
            user.user_id,
            prefer_web_app=supports_web_app(message.chat.type),
        ),
    )

    # Автоудаление информационного сообщения (чистота чата).
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=message.from_user.id if message.from_user else user.user_id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
        ttl_seconds=180,
    )


# Короткие алиасы вроде «я» убраны: они ложно срабатывали на обычную речь.
@router.message(RuCommand("профиль", "profile", "проф"))
async def profile_command(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает профиль игрока с кнопкой на сайт."""
    from app.repositories.users import get_user

    user_tg = message.from_user
    if user_tg is None:
        return

    user = await get_user(session, user_tg.id)
    if user is None:
        await message.answer(texts.USER_NOT_FOUND)
        return

    await _send_profile(message, session, user)


@router.message(RuCommand("кто"))
async def who_are_you_command(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Быстрый просмотр профиля по reply: «кто ты» в ответ на сообщение.

    Работает только как ответ (reply) на сообщение реального человека и только
    на точную фразу «кто ты». Иначе — молчим, чтобы не мешать обычной речи.
    """
    from app.repositories.users import get_user

    if command_args.strip().lower() != "ты":
        return
    reply = message.reply_to_message
    if reply is None or reply.from_user is None or reply.from_user.is_bot:
        return

    user = await get_user(session, reply.from_user.id)
    if user is None:
        await message.answer(texts.USER_NOT_FOUND)
        return

    await _send_profile(message, session, user)


