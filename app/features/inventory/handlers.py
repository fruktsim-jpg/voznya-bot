"""Хендлеры команды инвентаря: «инвентарь» / «инв» / «рюкзак».

Без аргументов — свой инвентарь; в ответ на сообщение или с @username/ID —
чужой. Поддержана простая пагинация: «инв 2» открывает вторую страницу.
Просмотр только; выдача предметов идёт через админку/магазин/кейсы.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.targets import resolve_target
from app.features.inventory.service import render_inventory
from app.repositories import inventory as inv_repo
from app.repositories import users as users_repo
from app.services.deletion import get_deletion_service
from app.settings import inventory as inv_texts

router = Router(name="inventory")


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
    text = render_inventory(
        rows,
        total,
        user_id=user_id,
        first_name=first_name,
        username=username,
        page=page,
        pages=pages,
    )

    sent = await message.answer(text)

    # Автоудаление информационного сообщения (чистота чата) — как в profile.
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=sender.id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
    )
