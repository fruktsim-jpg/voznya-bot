"""Хендлеры команды инвентаря: «инвентарь» / «инв» / «рюкзак».

Без аргументов — свой инвентарь; в ответ на сообщение или с @username/ID —
чужой. Поддержана простая пагинация: «инв 2» открывает вторую страницу.
Просмотр только; выдача предметов идёт через админку/магазин/кейсы.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.keyboards import inventory_pagination, supports_web_app
from app.core.money import money
from app.core.responses import send_info_window
from app.core.targets import resolve_target

from app.features.inventory.service import render_inventory
from app.repositories import gifts as gifts_repo
from app.repositories import inventory as inv_repo
from app.repositories import users as users_repo
from app.settings import inventory as inv_texts
from app.settings.balance import ESHKI_PER_STAR, ITEM_SELL_RATE


router = Router(name="inventory")


async def _render_inventory_page(
    session: AsyncSession,
    *,
    user_id: int,
    first_name: str | None,
    username: str | None,
    page: int,
) -> tuple[str, int, int]:
    """Returns inventory text, current page, and total pages for one player."""
    total = await inv_repo.count_items(session, user_id)
    distinct = await inv_repo.count_distinct_items(session, user_id)

    page_size = inv_texts.PAGE_SIZE
    pages = max(1, (distinct + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    offset = (page - 1) * page_size

    rows = await inv_repo.get_inventory(session, user_id, limit=page_size, offset=offset)
    gifts = await gifts_repo.get_pending_gifts_for_user(session, user_id) if page == 1 else []

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
    return text, page, pages


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

    page = _parse_page(command_args)
    text, page, pages = await _render_inventory_page(
        session,
        user_id=user_id,
        first_name=first_name,
        username=username,
        page=page,
    )

    # Site-first (Release 2.2): инвентарь в боте — быстрый просмотр, но основные
    # действия (продать/вывести/подарить/Premium) удобнее на сайте. Кнопку на
    # полный инвентарь показываем только владельцу (свой профиль).
    markup = None
    is_own = target is None or target.user_id == sender.id
    if is_own:
        url = f"{get_settings().website_url}/inventory"
        markup = inventory_pagination(
            page, pages, sender.id, url, prefer_web_app=supports_web_app(message.chat.type)
        )

    await send_info_window(
        session,
        message,
        "inventory",
        text,
        reply_markup=markup,
    )


@router.callback_query(F.data.startswith("inv:page:"))
async def cb_inventory_page(callback: CallbackQuery, session: AsyncSession) -> None:
    """Switches own inventory pages from inline buttons."""
    if callback.from_user is None or callback.message is None or callback.data is None:
        return

    _, _, owner_id_raw, page_raw = callback.data.split(":", maxsplit=3)
    owner_id = int(owner_id_raw)
    if callback.from_user.id != owner_id:
        await callback.answer("Это не твой инвентарь 🙅", show_alert=True)
        return

    page = int(page_raw)
    text, page, pages = await _render_inventory_page(
        session,
        user_id=owner_id,
        first_name=callback.from_user.first_name,
        username=callback.from_user.username,
        page=page,
    )
    url = f"{get_settings().website_url}/inventory"
    await callback.message.edit_text(
        text,
        reply_markup=inventory_pagination(
            page,
            pages,
            owner_id,
            url,
            prefer_web_app=supports_web_app(callback.message.chat.type),
        ),
    )
    await callback.answer()
