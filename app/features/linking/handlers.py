"""Хендлер привязки аккаунта сайта к игроку через deep-link.

Срабатывает на ``/start link_<token>`` (в т.ч. в личном чате — для этого в
``ChatFilterMiddleware`` сделано узкое исключение). Берёт НАСТОЯЩИЙ Telegram id
из ``message.from_user.id`` и подтверждает связь ``oidc_sub -> user_id``.

Должен быть зарегистрирован ДО ``help_router``: общий фильтр ``/start`` там
ловит любой старт, поэтому привязочный старт перехватываем здесь специальным
фильтром, который срабатывает только при наличии payload ``link_<token>``.
"""

from __future__ import annotations

import re

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.account_links import LinkResult, consume_link_request

router = Router(name="linking")

# Токен генерирует сайт: URL-safe, ограничен длиной (Telegram payload ≤ 64).
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,48}$")


class StartLinkFilter(BaseFilter):
    """Матчит ``/start link_<token>`` и отдаёт ``link_token`` в хендлер."""

    async def __call__(self, message: Message) -> bool | dict:
        text = (message.text or "").strip()
        parts = text.split(maxsplit=1)
        if not parts:
            return False

        command = parts[0]
        if command.startswith("/"):
            command = command[1:]
        if "@" in command:
            command = command.split("@", 1)[0]
        if command.lower() != "start":
            return False

        payload = parts[1].strip() if len(parts) > 1 else ""
        if not payload.startswith("link_"):
            return False

        token = payload[len("link_"):]
        if not _TOKEN_RE.match(token):
            return False
        return {"link_token": token}


@router.message(StartLinkFilter())
async def cmd_start_link(
    message: Message, session: AsyncSession, link_token: str
) -> None:
    """Подтверждает привязку OIDC-аккаунта к этому Telegram-пользователю."""
    user = message.from_user
    if user is None:
        return

    outcome = await consume_link_request(session, link_token, user.id)

    if outcome.result is LinkResult.LINKED:
        await message.answer(
            "✅ Аккаунт привязан!\n\n"
            "Вернитесь на сайт и обновите страницу — теперь вход через "
            "Telegram открывает ваш профиль Возни."
        )
    elif outcome.result is LinkResult.EXPIRED:
        await message.answer(
            "⌛️ Ссылка для привязки устарела.\n\n"
            "Войдите на сайте ещё раз, чтобы получить свежую ссылку."
        )
    else:  # NOT_FOUND
        await message.answer(
            "🤔 Ссылка для привязки недействительна или уже использована.\n\n"
            "Войдите на сайте ещё раз, чтобы получить новую ссылку."
        )
