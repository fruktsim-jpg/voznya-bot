"""Бизнес-логика модерации: парсинг длительностей, проверка прав, применение
ограничений через Telegram и запись аудита.

Разделение ответственности:
* чистые функции (``parse_duration``, ``format_duration``) — без БД и сети,
  легко тестируются;
* функции применения (``apply_ban``/``apply_mute``/...) принимают ``bot`` и
  ``session`` — они и есть «толстый» слой, дергающий Telegram + БД + аудит.

Право на действие = Telegram-админ чата (бот спрашивает у Telegram) ИЛИ роль
на админ-платформе с нужным правом (``admin_roles`` + permissions), ИЛИ
bootstrap-owner из ADMIN_IDS. Это наконец «оживляет» дремавший RBAC, не ломая
существующий env-путь.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatPermissions
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import permissions
from app.core.logger import get_logger
from app.core.utils import now_utc
from app.repositories import moderation as mod_repo
from app.settings import moderation as mod_settings

logger = get_logger(__name__)


# --- Чистые функции ---------------------------------------------------------


# Сентинел «навсегда» для длительностей (отличаем от «не указано» = None).
class _Permanent:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - для дебага
        return "PERMANENT"


PERMANENT = _Permanent()


def parse_duration(token: str | None) -> int | None | object:
    """Разбирает длительность вида ``10m`` / ``2h`` / ``1d`` / ``0`` (навсегда).

    Возвращает:
    * ``int`` — число секунд (> 0);
    * ``PERMANENT`` (сентинел) — если задано «0»/«навсегда»/«forever»/«perm»;
    * ``None`` — если токен не похож на длительность (тогда вызывающий код
      берёт дефолт).
    """
    if not token:
        return None
    t = token.strip().lower()
    if t in {"0", "навсегда", "forever", "perm", "permanent", "∞"}:
        return PERMANENT

    # Голое число без суффикса трактуем как минуты (привычно для модерации).
    if t.isdigit():
        minutes = int(t)
        return minutes * 60 if minutes > 0 else PERMANENT

    unit = t[-1]
    if unit not in mod_settings.DURATION_UNITS:
        return None
    body = t[:-1]
    if not body.isdigit():
        return None
    value = int(body)
    if value <= 0:
        return PERMANENT
    return value * mod_settings.DURATION_UNITS[unit]


def format_duration(seconds: int | None) -> str:
    """Человекочитаемая длительность («навсегда» при None)."""
    if seconds is None:
        return "навсегда"
    total = int(seconds)
    if total <= 0:
        return "навсегда"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if secs and not parts:
        parts.append(f"{secs} сек")
    return " ".join(parts) or "навсегда"


# Сентинел «навсегда» для длительностей (отличаем от «не указано» = None).
class _Permanent:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - для дебага
        return "PERMANENT"


PERMANENT = _Permanent()


def resolve_until(parsed: int | None | object, default_seconds: int) -> datetime | None:
    """По результату parse_duration возвращает абсолютное время окончания.

    None (не указано) → дефолт; PERMANENT → None (бессрочно); int → now+sec.
    """
    if parsed is PERMANENT:
        return None
    seconds = parsed if isinstance(parsed, int) else default_seconds
    return now_utc() + timedelta(seconds=seconds)


# --- Проверка прав ----------------------------------------------------------


@dataclass(slots=True)
class ModContext:
    """Кто инициатор действия и какова его роль (для аудита и проверки прав)."""

    actor_user_id: int
    actor_role: str | None


async def can_moderate(
    session: AsyncSession, bot: Bot, chat_id: int, user_id: int, permission: str
) -> ModContext | None:
    """Возвращает контекст актора, если он вправе модерировать, иначе None.

    Право даёт ЛЮБОЙ из источников:
    1. bootstrap-owner из ADMIN_IDS (env) — аварийный суперпользователь;
    2. роль на админ-платформе с нужным permission (admin_roles + RBAC);
    3. статус админа Telegram-чата (creator/administrator).
    """
    settings = get_settings()
    role = await mod_repo.get_role(session, user_id)

    if settings.is_admin(user_id):
        return ModContext(actor_user_id=user_id, actor_role=role or "owner")

    if permissions.has_permission(role, permission):
        return ModContext(actor_user_id=user_id, actor_role=role)

    # Telegram-админ чата (без платформенной роли) — тоже модератор по факту.
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in {"creator", "administrator"}:
            return ModContext(actor_user_id=user_id, actor_role=role)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass
    return None


async def is_target_protected(
    session: AsyncSession, bot: Bot, chat_id: int, target_user_id: int
) -> bool:
    """Нельзя модерировать других админов/владельца (защита от «войн админов»)."""
    settings = get_settings()
    if settings.is_admin(target_user_id):
        return True
    role = await mod_repo.get_role(session, target_user_id)
    if role in {"owner", "admin"}:
        return True
    try:
        member = await bot.get_chat_member(chat_id, target_user_id)
        if member.status in {"creator", "administrator"}:
            return True
    except (TelegramBadRequest, TelegramForbiddenError):
        pass
    return False


# --- Применение ограничений -------------------------------------------------

# Полностью «немой» набор прав для мьюта (запрещаем всё, что можно).
_MUTED_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)

# Возврат к обычным правам (снятие мьюта).
_UNMUTED_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)


async def apply_mute_telegram(
    bot: Bot, chat_id: int, user_id: int, until: datetime | None
) -> bool:
    """Реальный мьют через restrictChatMember. True — успех, False — нет прав."""
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=_MUTED_PERMS,
            until_date=_clamp_until(until),
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning("Не удалось замьютить %s: %s", user_id, exc)
        return False


async def lift_mute_telegram(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Снимает мьют (возвращает обычные права)."""
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id, permissions=_UNMUTED_PERMS
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning("Не удалось снять мьют у %s: %s", user_id, exc)
        return False


async def apply_ban_telegram(
    bot: Bot, chat_id: int, user_id: int, until: datetime | None
) -> bool:
    """Реальный бан через banChatMember."""
    try:
        await bot.ban_chat_member(
            chat_id=chat_id, user_id=user_id, until_date=_clamp_until(until)
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning("Не удалось забанить %s: %s", user_id, exc)
        return False


async def lift_ban_telegram(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Снимает бан (unban с only_if_banned, чтобы не «приглашать» обратно)."""
    try:
        await bot.unban_chat_member(
            chat_id=chat_id, user_id=user_id, only_if_banned=True
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning("Не удалось снять бан у %s: %s", user_id, exc)
        return False


async def kick_telegram(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Кик = бан + немедленный разбан (человек может вернуться по ссылке)."""
    ok = await apply_ban_telegram(bot, chat_id, user_id, until=None)
    if ok:
        await lift_ban_telegram(bot, chat_id, user_id)
    return ok


def _clamp_until(until: datetime | None) -> datetime | None:
    """Ограничивает срок максимум MAX_RESTRICT_SECONDS (Telegram-лимит)."""
    if until is None:
        return None
    max_until = now_utc() + timedelta(seconds=mod_settings.MAX_RESTRICT_SECONDS)
    return min(until, max_until)
