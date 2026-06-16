"""Фоновое сопровождение модерации: авто-снятие истёкших ограничений и
лёгкий backstop-энфорсмент мьюта.

Почему это нужно, если Telegram сам снимает restrict по ``until_date``:
* БД-состояние (``user_moderation``) должно оставаться правдивым для ``/modinfo``
  и админ-панели сайта — иначе там «висят» протухшие баны/мьюты;
* если в момент команды у бота не было прав админа, Telegram ограничение не
  применил, но в БД мьют записан — middleware-backstop удаляет сообщения таких
  игроков, пока мьют активен.

In-memory множество замьюченных обновляется тем же тиком планировщика (раз в
минуту), поэтому per-message обращений к БД нет.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.moderation import service
from app.models import UserModeration
from app.repositories import moderation as mod_repo

logger = get_logger(__name__)

# Множество user_id с активным мьютом (backstop). Обновляется планировщиком.
_muted_now: set[int] = set()


class MuteEnforcementMiddleware(BaseMiddleware):
    """Backstop: удаляет сообщения замьюченных игроков (если Telegram не снял).

    Дёшево: проверяет только in-memory множество, в БД не ходит.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.from_user is not None
            and event.from_user.id in _muted_now
            and event.chat is not None
            and event.chat.type != "private"
        ):
            try:
                await event.delete()
            except TelegramBadRequest:
                pass
            # Сообщение замьюченного — дальше по цепочке не пускаем.
            return None
        return await handler(event, data)


def setup_moderation_scheduler(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    chat_id: int,
) -> None:
    """Регистрирует периодическую задачу авто-снятия и обновления кэша мьютов."""

    async def _tick() -> None:
        try:
            await _sync(bot, sessionmaker, chat_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Тик модерации завершился ошибкой: %s", exc)

    scheduler.add_job(
        _tick,
        trigger="interval",
        minutes=1,
        id="moderation_sync",
        replace_existing=True,
        misfire_grace_time=120,
    )


async def _sync(
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    chat_id: int,
) -> None:
    """Снимает истёкшие баны/мьюты в БД и Telegram, обновляет кэш мьютов."""
    async with sessionmaker() as session:
        # 0) Применить изменения, пришедшие с сайта (tg_pending): сайт не может
        #    дёргать Telegram сам, поэтому бот доводит состояние до Telegram.
        for state in await mod_repo.pending_tg(session):
            await _reconcile_telegram(bot, chat_id, state)
            await mod_repo.clear_tg_pending(session, state.user_id)

        # 1) Истёкшие баны → снять в Telegram и обнулить в БД.
        for user_id in await mod_repo.due_unbans(session):
            await service.lift_ban_telegram(bot, chat_id, user_id)
            await mod_repo.set_ban(session, user_id, None, None, None)

        # 2) Истёкшие мьюты → снять в Telegram и обнулить в БД.
        for user_id in await mod_repo.due_unmutes(session):
            await service.lift_mute_telegram(bot, chat_id, user_id)
            await mod_repo.set_mute(session, user_id, None, None, None)

        await session.commit()

        # 3) Обновить in-memory множество активных мьютов (для backstop).
        now = now_utc()
        result = await session.execute(
            select(UserModeration.user_id).where(
                UserModeration.muted_until.is_not(None),
                UserModeration.muted_until > now,
            )
        )
        global _muted_now
        _muted_now = {row[0] for row in result.all()}


async def _reconcile_telegram(bot: Bot, chat_id: int, state: UserModeration) -> None:
    """Доводит состояние одной записи до Telegram (для сайт-изменений).

    Идемпотентно: применяет активный бан/мьют либо снимает истёкший/снятый.
    Мьют дополнительно подстрахован backstop-middleware, но restrict тут даёт
    «настоящий» мьют на стороне Telegram.
    """
    now = now_utc()

    # Бан имеет приоритет: активный бан → ban; иначе снять бан.
    if state.banned_until is not None and state.banned_until > now:
        await service.apply_ban_telegram(bot, chat_id, state.user_id, state.banned_until)
        return
    await service.lift_ban_telegram(bot, chat_id, state.user_id)

    # Мьют: активный → restrict; иначе вернуть права.
    if state.muted_until is not None and state.muted_until > now:
        await service.apply_mute_telegram(bot, chat_id, state.user_id, state.muted_until)
    else:
        await service.lift_mute_telegram(bot, chat_id, state.user_id)
