"""Команды модерации чата: бан/мьют/варн/кик и просмотр состояния.

Тонкий слой над ``service.py`` и ``repositories/moderation.py``:
* резолвит цель (reply/@username/id) через общий ``targets.py``;
* проверяет права (Telegram-админ ИЛИ роль платформы ИЛИ owner из ADMIN_IDS);
* применяет ограничение в Telegram и пишет состояние + аудит в БД.

Все действия логируются в ``audit_log`` (player.ban/unban/mute/unmute/warn/
unwarn/kick) — попадают в ленту админ-панели сайта.
"""

from __future__ import annotations

from datetime import timedelta

from aiogram import Bot, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions
from app.core.filters import RuCommand
from app.core.targets import resolve_target
from app.core.utils import escape, mention, now_utc, to_local
from app.features.moderation import service
from app.models import User
from app.repositories import moderation as mod_repo
from app.settings import moderation as mod_settings
from app.settings import texts

router = Router(name="moderation")


def _split_duration_reason(
    message: Message, command_args: str
) -> tuple[str | None, str | None]:
    """Достаёт (срок, причину) из аргументов команды.

    Если цель указана через reply — все аргументы это [срок?] [причина...].
    Если цель указана через @username/id (первый токен) — пропускаем его.
    Срок распознаётся только если первый рассматриваемый токен парсится как
    длительность; иначе всё считается причиной.
    """
    parts = command_args.split() if command_args else []
    has_reply = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and not message.reply_to_message.from_user.is_bot
    )
    if not has_reply and parts:
        # Первый токен — указание цели (@user / id), убираем его.
        parts = parts[1:]

    if not parts:
        return None, None

    first = parts[0]
    if service.parse_duration(first) is not None:
        duration = first
        reason = " ".join(parts[1:]).strip() or None
    else:
        duration = None
        reason = " ".join(parts).strip() or None
    return duration, reason


def _reason_only(message: Message, command_args: str) -> str | None:
    """Достаёт только причину (для команд без длительности: warn/unwarn/kick).

    В отличие от _split_duration_reason, НЕ трактует первый токен как срок —
    у этих команд срока нет, поэтому «5» в причине останется причиной.
    """
    parts = command_args.split() if command_args else []
    has_reply = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and not message.reply_to_message.from_user.is_bot
    )
    if not has_reply and parts:
        parts = parts[1:]  # первый токен — указание цели (@user / id)
    return " ".join(parts).strip() or None


def _reason_suffix(reason: str | None) -> str:
    return texts.MOD_REASON_SUFFIX.format(reason=escape(reason)) if reason else ""


def _m(target: User) -> str:
    return mention(target.user_id, target.first_name, target.username)


async def _guard(
    message: Message,
    session: AsyncSession,
    bot: Bot,
    command_args: str,
    permission: str,
) -> tuple[service.ModContext, User] | None:
    """Общая преамбула: чат-гейт, права актора, резолв и защита цели.

    Возвращает (контекст актора, цель) или None (с уже отправленным ответом).
    """
    if message.from_user is None or message.chat is None:
        return None

    ctx = await service.can_moderate(
        session, bot, message.chat.id, message.from_user.id, permission
    )
    if ctx is None:
        await message.answer(texts.MOD_DENIED)
        return None

    target = await resolve_target(session, message, command_args)
    if target is None:
        await message.answer(texts.MOD_NO_TARGET)
        return None

    if await service.is_target_protected(session, bot, message.chat.id, target.user_id):
        await message.answer(texts.MOD_TARGET_PROTECTED)
        return None

    return ctx, target


@router.message(RuCommand("бан", "ban"))
async def cmd_ban(
    message: Message, session: AsyncSession, bot: Bot, command_args: str
) -> None:
    guarded = await _guard(
        message, session, bot, command_args, permissions.PERM_MODERATION_BAN
    )
    if guarded is None:
        return
    ctx, target = guarded

    duration, reason = _split_duration_reason(message, command_args)
    parsed = service.parse_duration(duration)
    until = service.resolve_until(parsed, mod_settings.MAX_RESTRICT_SECONDS)
    seconds = None if until is None else int((until - now_utc()).total_seconds())

    ok = await service.apply_ban_telegram(bot, message.chat.id, target.user_id, until)
    await mod_repo.set_ban(session, target.user_id, until, reason, ctx.actor_user_id)
    await mod_repo.write_audit(
        session,
        actor_user_id=ctx.actor_user_id,
        actor_role=ctx.actor_role,
        action="player.ban",
        target_user_id=target.user_id,
        reason=reason,
        meta={"until": until.isoformat() if until else None, "source": "bot"},
    )

    text = texts.MOD_BANNED.format(
        mention=_m(target),
        duration=service.format_duration(seconds),
        reason=_reason_suffix(reason),
    )
    if not ok:
        text += texts.MOD_RESTRICT_FAILED
    await message.answer(text)


@router.message(RuCommand("разбан", "unban"))
async def cmd_unban(
    message: Message, session: AsyncSession, bot: Bot, command_args: str
) -> None:
    guarded = await _guard(
        message, session, bot, command_args, permissions.PERM_MODERATION_BAN
    )
    if guarded is None:
        return
    ctx, target = guarded

    await service.lift_ban_telegram(bot, message.chat.id, target.user_id)
    await mod_repo.set_ban(session, target.user_id, None, None, ctx.actor_user_id)
    await mod_repo.write_audit(
        session,
        actor_user_id=ctx.actor_user_id,
        actor_role=ctx.actor_role,
        action="player.unban",
        target_user_id=target.user_id,
        meta={"source": "bot"},
    )
    await message.answer(texts.MOD_UNBANNED.format(mention=_m(target)))


@router.message(RuCommand("мут", "mute"))
async def cmd_mute(
    message: Message, session: AsyncSession, bot: Bot, command_args: str
) -> None:
    guarded = await _guard(
        message, session, bot, command_args, permissions.PERM_MODERATION_BAN
    )
    if guarded is None:
        return
    ctx, target = guarded

    duration, reason = _split_duration_reason(message, command_args)
    parsed = service.parse_duration(duration)
    until = service.resolve_until(parsed, mod_settings.DEFAULT_MUTE_SECONDS)
    seconds = None if until is None else int((until - now_utc()).total_seconds())

    ok = await service.apply_mute_telegram(bot, message.chat.id, target.user_id, until)
    await mod_repo.set_mute(session, target.user_id, until, reason, ctx.actor_user_id)
    await mod_repo.write_audit(
        session,
        actor_user_id=ctx.actor_user_id,
        actor_role=ctx.actor_role,
        action="player.mute",
        target_user_id=target.user_id,
        reason=reason,
        meta={"until": until.isoformat() if until else None, "source": "bot"},
    )

    text = texts.MOD_MUTED.format(
        mention=_m(target),
        duration=service.format_duration(seconds),
        reason=_reason_suffix(reason),
    )
    if not ok:
        text += texts.MOD_RESTRICT_FAILED
    await message.answer(text)


@router.message(RuCommand("размут", "unmute"))
async def cmd_unmute(
    message: Message, session: AsyncSession, bot: Bot, command_args: str
) -> None:
    guarded = await _guard(
        message, session, bot, command_args, permissions.PERM_MODERATION_BAN
    )
    if guarded is None:
        return
    ctx, target = guarded

    await service.lift_mute_telegram(bot, message.chat.id, target.user_id)
    await mod_repo.set_mute(session, target.user_id, None, None, ctx.actor_user_id)
    await mod_repo.write_audit(
        session,
        actor_user_id=ctx.actor_user_id,
        actor_role=ctx.actor_role,
        action="player.unmute",
        target_user_id=target.user_id,
        meta={"source": "bot"},
    )
    await message.answer(texts.MOD_UNMUTED.format(mention=_m(target)))


@router.message(RuCommand("варн", "warn", "пред"))
async def cmd_warn(
    message: Message, session: AsyncSession, bot: Bot, command_args: str
) -> None:
    guarded = await _guard(
        message, session, bot, command_args, permissions.PERM_MODERATION_BAN
    )
    if guarded is None:
        return
    ctx, target = guarded

    reason = _reason_only(message, command_args)
    expires_at = (
        now_utc() + timedelta(seconds=mod_settings.WARN_TTL_SECONDS)
        if mod_settings.WARN_TTL_SECONDS > 0
        else None
    )
    count = await mod_repo.add_warning(
        session, target.user_id, ctx.actor_user_id, reason, expires_at
    )
    await mod_repo.write_audit(
        session,
        actor_user_id=ctx.actor_user_id,
        actor_role=ctx.actor_role,
        action="player.warn",
        target_user_id=target.user_id,
        reason=reason,
        meta={"count": count, "source": "bot"},
    )

    text = texts.MOD_WARNED.format(
        mention=_m(target),
        count=count,
        threshold=mod_settings.WARN_MUTE_THRESHOLD,
        reason=_reason_suffix(reason),
    )

    # Порог варнов → авто-мьют.
    if count >= mod_settings.WARN_MUTE_THRESHOLD:
        until = service.resolve_until(None, mod_settings.WARN_MUTE_SECONDS)
        await service.apply_mute_telegram(bot, message.chat.id, target.user_id, until)
        await mod_repo.set_mute(
            session, target.user_id, until, "авто-мьют по варнам", ctx.actor_user_id
        )
        await mod_repo.write_audit(
            session,
            actor_user_id=ctx.actor_user_id,
            actor_role=ctx.actor_role,
            action="player.mute",
            target_user_id=target.user_id,
            reason="авто-мьют по варнам",
            meta={"auto": True, "warns": count, "source": "bot"},
        )
        text += texts.MOD_WARN_AUTOMUTE.format(
            duration=service.format_duration(mod_settings.WARN_MUTE_SECONDS)
        )
    await message.answer(text)


@router.message(RuCommand("снятьварн", "unwarn"))
async def cmd_unwarn(
    message: Message, session: AsyncSession, bot: Bot, command_args: str
) -> None:
    guarded = await _guard(
        message, session, bot, command_args, permissions.PERM_MODERATION_BAN
    )
    if guarded is None:
        return
    ctx, target = guarded

    cleared = await mod_repo.clear_warnings(session, target.user_id, ctx.actor_user_id)
    await mod_repo.write_audit(
        session,
        actor_user_id=ctx.actor_user_id,
        actor_role=ctx.actor_role,
        action="player.unwarn",
        target_user_id=target.user_id,
        meta={"cleared": cleared, "source": "bot"},
    )
    await message.answer(
        texts.MOD_UNWARNED.format(mention=_m(target), cleared=cleared)
    )


@router.message(RuCommand("кик", "kick"))
async def cmd_kick(
    message: Message, session: AsyncSession, bot: Bot, command_args: str
) -> None:
    guarded = await _guard(
        message, session, bot, command_args, permissions.PERM_MODERATION_BAN
    )
    if guarded is None:
        return
    ctx, target = guarded

    reason = _reason_only(message, command_args)
    ok = await service.kick_telegram(bot, message.chat.id, target.user_id)
    await mod_repo.write_audit(
        session,
        actor_user_id=ctx.actor_user_id,
        actor_role=ctx.actor_role,
        action="player.kick",
        target_user_id=target.user_id,
        reason=reason,
        meta={"source": "bot"},
    )
    text = texts.MOD_KICKED.format(mention=_m(target))
    if not ok:
        text += texts.MOD_RESTRICT_FAILED
    await message.answer(text)


@router.message(RuCommand("modinfo", "модинфо"))
async def cmd_modinfo(
    message: Message, session: AsyncSession, bot: Bot, command_args: str
) -> None:
    """Показывает текущие ограничения и последние варны игрока."""
    if message.from_user is None or message.chat is None:
        return
    ctx = await service.can_moderate(
        session, bot, message.chat.id, message.from_user.id, permissions.PERM_MODERATION_VIEW
    )
    if ctx is None:
        await message.answer(texts.MOD_DENIED)
        return

    target = await resolve_target(session, message, command_args)
    if target is None:
        await message.answer(texts.MOD_INFO_USAGE)
        return

    await mod_repo.expire_warnings(session, target.user_id)
    state = await mod_repo.get_state(session, target.user_id)
    warns = await mod_repo.list_warnings(session, target.user_id, limit=5)

    def _fmt_until(dt) -> str:
        if dt is None:
            return "—"
        return to_local(dt).strftime("%d.%m %H:%M")

    ban_str = "нет" if state is None or state.banned_until is None else (
        f"до {_fmt_until(state.banned_until)}"
    )
    mute_str = "нет" if state is None or state.muted_until is None else (
        f"до {_fmt_until(state.muted_until)}"
    )
    warn_count = 0 if state is None else state.warn_count

    lines = [
        texts.MOD_INFO.format(
            mention=_m(target),
            user_id=target.user_id,
            ban=ban_str,
            mute=mute_str,
            warns=warn_count,
        )
    ]
    for w in warns:
        lines.append(
            texts.MOD_INFO_WARN_LINE.format(
                when=_fmt_until(w.created_at),
                reason=escape(w.reason) or "без причины",
            )
        )
    await message.answer("\n".join(lines))
