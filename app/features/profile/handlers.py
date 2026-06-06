"""Обработчики команды /profile — показ профиля игрока."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.models import User
from app.settings import texts
from app.settings.titles import get_title, get_next_title


router = Router(name="profile")


async def render_profile(session: AsyncSession, user: User) -> str:
    """Формирует текст профиля игрока."""
    from app.features.achievements.service import get_unlocked_codes
    from app.repositories.combot_stats import get_combot_overlay, total_messages
    from app.repositories.marriages import get_active_marriage
    from app.repositories.users import get_user
    from app.settings.achievements import ACHIEVEMENTS
    
    settings = get_settings()
    title = get_title(user.total_earned)
    next_title = get_next_title(user.total_earned)

    # Единый счётчик сообщений: историческая надстройка Combot + счёт Возни.
    # Для игрока это одна цифра за всю историю сообщества, без упоминания Combot.
    overlay = await get_combot_overlay(session, user.user_id)
    messages_total = total_messages(user.messages_count, overlay)

    # MMR — отдельный игровой рейтинг (общий прогресс), не связан с ешками.
    from app.repositories.mmr import get_mmr
    from app.settings import mmr as mmr_settings

    mmr_value = await get_mmr(session, user.user_id)
    rank = mmr_settings.get_rank(mmr_value)

    text = (
        f"👤 <b>Профиль — {user.display_name()}</b>\n\n"
        f"💰 Баланс: <b>{user.balance:,}</b> ешек\n"
        f"📈 Заработано: <b>{user.total_earned:,}</b> ешек\n"
        f"💬 Сообщений: <b>{messages_total:,}</b>\n"
        f"🏆 Титул: {title.emoji} <b>{title.name}</b>\n"
        + mmr_settings.PROFILE_MMR_LINE.format(mmr=mmr_value)
        + mmr_settings.PROFILE_RANK_LINE.format(
            rank_emoji=rank.emoji, rank_name=rank.name
        )
    )


    
    # Компактная строка «в чате с» — только если Combot знает дату входа.
    if overlay.joined_at is not None:
        text += f"📅 В чате с: {overlay.joined_at:%d.%m.%Y}\n"

    # Прогресс до следующего титула
    if next_title:

        progress = user.total_earned - title.min_earned
        needed = next_title.min_earned - title.min_earned
        percent = int((progress / needed) * 100) if needed > 0 else 100
        text += f"📊 Прогресс: {percent}% до {next_title.emoji} {next_title.name}\n"
    
    # Достижения
    unlocked = await get_unlocked_codes(session, user.user_id)
    total_achievements = len(ACHIEVEMENTS)
    opened_achievements = len(unlocked)
    text += f"🏅 Достижения: {opened_achievements}/{total_achievements}\n"

    # Инвентарь — суммарное число предметов (read-only из inventory).
    from app.repositories.inventory import count_items
    from app.settings import inventory as inv_settings

    items_count = await count_items(session, user.user_id)
    if items_count:
        text += inv_settings.PROFILE_ITEMS_LINE.format(count=items_count)
    
    # Брак
    marriage = await get_active_marriage(session, user.user_id)
    if marriage:
        partner_id = marriage.user_id_2 if marriage.user_id_1 == user.user_id else marriage.user_id_1
        partner = await get_user(session, partner_id)
        if partner:
            partner_name = partner.display_name()
            text += f"💍 В браке с {partner_name}\n"
    
    text += (
        f"\n⚔️ Дуэли: {user.duels_won} побед / {user.duels_lost} поражений\n"
        f"🌾 Серия фермы: {user.farm_streak} (рекорд: {user.max_farm_streak})\n"
        f"📦 Кладов найдено: {user.treasures_found}"
    )
    
    return text



async def _send_profile(message: Message, session: AsyncSession, user: User) -> None:
    """Отправляет карточку профиля игрока с кнопкой на сайт + автоудаление."""
    from app.services.deletion import get_deletion_service

    settings = get_settings()
    text = await render_profile(session, user)

    profile_url = f"{settings.website_url}/profile/{user.user_id}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Открыть профиль на сайте", url=profile_url)]
        ]
    )

    sent = await message.answer(text, reply_markup=keyboard)

    # Автоудаление информационного сообщения (чистота чата).
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=message.from_user.id if message.from_user else user.user_id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
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


