"""Онбординг в личке и приём подарка по ссылке (claim-flow).

Закрывает два пробела (Release 2.2):

1. Любой игрок может написать боту в ЛС и пройти онбординг (`/start`, `/help`).
   Раньше личка была закрыта для не-админов (см. ChatFilterMiddleware) — теперь
   открыта для команд онбординга.
2. «Подарить другу по ссылке» для тех, кто НЕ запускал бота: отправитель с сайта
   создаёт pending-доставку с ``meta.claim_token`` и получает ссылку
   ``https://t.me/<bot>?start=gift_<token>``. Получатель открывает её, запускает
   бота и забирает РЕАЛЬНЫЙ подарок — выдаётся ему через тот же конвейер
   ``deliver_gift`` (claim_gift_by_token).

Регистрируется ДО help_router, чтобы перехватить ``/start gift_<token>`` своим
фильтром (общий ``/start`` в help ловит любой старт). Обычный ``/start`` без
payload отдаём общему приветствию (help_router его тоже обрабатывает).
"""

from __future__ import annotations

import re

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.features.gifts.service import claim_gift_by_token

router = Router(name="gift_claim")

# Токен claim-ссылки генерирует сайт: URL-safe, ≤ payload-лимита Telegram (64).
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,48}$")

# --- Тексты -----------------------------------------------------------------
CLAIM_OK = "🎁 Лови подарок: <b>{gift}</b> уже летит тебе в Telegram!"
CLAIM_PENDING = (
    "🎁 Подарок <b>{gift}</b> почти у тебя — выдаю, загляни в Telegram чуть позже."
)
CLAIM_CANCELLED = "⚠️ Не получилось выдать подарок. Отправитель получил возврат."
CLAIM_NOT_FOUND = (
    "🤷 Эта ссылка на подарок недействительна или его уже забрали.\n"
    "Если думаешь, что это ошибка — напиши тому, кто прислал ссылку."
)


class StartGiftFilter(BaseFilter):
    """Матчит ``/start gift_<token>`` и отдаёт ``claim_token`` в хендлер."""

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
        if not payload.startswith("gift_"):
            return False
        token = payload[len("gift_"):]
        if not _TOKEN_RE.match(token):
            return False
        return {"claim_token": token}


@router.message(StartGiftFilter())
async def cmd_start_gift(
    message: Message, session: AsyncSession, claim_token: str
) -> None:
    """Принимает подарок по ссылке и реально отправляет его получателю."""
    user = message.from_user
    if user is None or message.bot is None:
        return

    settings = get_settings()
    outcome = await claim_gift_by_token(
        session,
        message.bot,
        claim_token=claim_token,
        claimer_user_id=user.id,
        enabled=settings.gifts_delivery_enabled,
        channel="bot",
    )
    # Результат коммитит DbSessionMiddleware при возврате из хендлера.

    gift = outcome_gift_name(outcome)
    if outcome.status == "completed":
        await message.answer(CLAIM_OK.format(gift=gift))
    elif outcome.status == "pending":
        await message.answer(CLAIM_PENDING.format(gift=gift))
    elif outcome.status == "cancelled":
        await message.answer(CLAIM_CANCELLED)
    else:  # skip / claim_not_found
        await message.answer(CLAIM_NOT_FOUND)


def outcome_gift_name(outcome) -> str:  # noqa: ANN001 — приватный хелпер рендера
    """Подпись подарка для сообщения (DeliverOutcome не несёт имя — даём общее)."""
    return "подарок"
