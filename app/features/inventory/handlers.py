"""Хендлеры команды инвентаря: «инвентарь» / «инв» / «рюкзак».

Без аргументов — свой инвентарь; в ответ на сообщение или с @username/ID —
чужой. Поддержана простая пагинация: «инв 2» открывает вторую страницу.
Просмотр только; выдача предметов идёт через админку/магазин/кейсы.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.keyboards import open_on_site
from app.core.money import money
from app.core.targets import resolve_target

from app.features.inventory.service import render_inventory
from app.repositories import gifts as gifts_repo
from app.repositories import inventory as inv_repo
from app.repositories import users as users_repo
from app.services.deletion import get_deletion_service
from app.settings import inventory as inv_texts
from app.settings.balance import ESHKI_PER_STAR, ITEM_SELL_RATE


router = Router(name="inventory")


def _gift_value(gift) -> int:
    """Стоимость подарка в ешках — единый курс (price_eshki, фолбэк star×курс).

    Совпадает с ``_item_full_value`` сервиса: база — цена магазина; если её нет
    (рассинхрон каталога) — внутренняя стоимость ``star_cost × ESHKI_PER_STAR``.
    """
    if gift is not None and (gift.price_eshki or 0) > 0:
        return int(gift.price_eshki)
    star_cost = int(gift.star_cost or 0) if gift is not None else 0
    return max(0, star_cost) * ESHKI_PER_STAR


def _render_gifts_section(gifts: list) -> str:
    """Блок «Подарки и Premium» для текста инвентаря (пусто → пустая строка)."""
    if not gifts:
        return ""
    lines = [inv_texts.INV_GIFTS_HEADER.format(count=len(gifts))]
    for delivery, gift in gifts:
        name = (gift.name if gift else None) or delivery.item_code or "подарок"
        value = _gift_value(gift)
        sell = int(max(0, value) * ITEM_SELL_RATE)
        lines.append(
            inv_texts.INV_GIFTS_ROW.format(
                name=name, value=money(value), sell=money(sell)
            )
        )
    lines.append(inv_texts.INV_GIFTS_HINT)
    return "\n" + "\n".join(lines)


def _parse_page(args: str) -> int:

    """Достаёт номер страницы из аргументов (последний числовой токен).

    «инв», «инв @user», «инв @user 2», «инв 2» — всё корректно. Любой мусор →
    страница 1. Цель пользователя разбирает resolve_target отдельно.
    """
    for token in reversed(args.split()):
        if token.isdigit():
            return max(1, int(token))
    return 1


@router.message(RuCommand("инвентарь", "инв", "рюкзак", "inventory", "inv"))
async def cmd_inventory(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Показывает инвентарь игрока (свой или указанного) с пагинацией."""
    sender = message.from_user
    if sender is None:
        return

    target = await resolve_target(session, message, command_args)
    if target is not None:
        user_id = target.user_id
        first_name = target.first_name
        username = target.username
    else:
        user = await users_repo.get_user(session, sender.id)
        user_id = user.user_id if user else sender.id
        first_name = sender.first_name
        username = sender.username

    total = await inv_repo.count_items(session, user_id)
    distinct = await inv_repo.count_distinct_items(session, user_id)

    page_size = inv_texts.PAGE_SIZE
    pages = max(1, (distinct + page_size - 1) // page_size)
    page = min(_parse_page(command_args), pages)
    offset = (page - 1) * page_size

    rows = await inv_repo.get_inventory(
        session, user_id, limit=page_size, offset=offset
    )

    # Подарки/Premium живут отдельно от стековых предметов (в gift_transactions,
    # status='pending') — это та же сущность, что показывает сайт. Показываем их,
    # чтобы инвентарь бота и сайта совпадал (единый источник правды). Только на
    # первой странице, чтобы не дублировать на каждой.
    gifts = (
        await gifts_repo.get_pending_gifts_for_user(session, user_id)
        if page == 1
        else []
    )

    text = render_inventory(
        rows,
        total,
        user_id=user_id,
        first_name=first_name,
        username=username,
        page=page,
        pages=pages,
        has_gifts=bool(gifts),
    )
    text += _render_gifts_section(gifts)

    # Site-first (Release 2.2): инвентарь в боте — быстрый просмотр, но основные
    # действия (продать/вывести/подарить/Premium) удобнее на сайте. Кнопку на
    # полный инвентарь показываем только владельцу (свой профиль).
    markup = None
    is_own = target is None or target.user_id == sender.id
    if is_own:
        url = f"{get_settings().website_url}/inventory"
        markup = open_on_site(inv_texts.INV_SITE_BTN, url)

    sent = await message.answer(text, reply_markup=markup)



    # Автоудаление информационного сообщения (чистота чата) — как в profile.
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=sender.id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
    )
