"""Middleware регистрации и трекинга активности пользователей.

На каждое сообщение от реального пользователя обновляет его запись в БД:
username, имя и время последней активности. Это формирует пул «активных»
участников для номинаций (Пидор/Пара дня).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from datetime import timedelta

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_local, now_utc
from app.repositories import messages as messages_repo
from app.repositories import users as users_repo
from app.settings import balance

logger = get_logger(__name__)


class UserTrackingMiddleware(BaseMiddleware):
    """Апсертит пользователя и отмечает его активность.

    Активность фиксируется как для сообщений, так и для нажатий на кнопки.
    Дополнительно выдаёт секретное достижение «Призрак Возни» тем, кто вернулся
    после долгого отсутствия (тихо, без уведомления — это секрет).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        is_message = isinstance(event, Message)
        if is_message or isinstance(event, CallbackQuery):
            user = event.from_user

        if user is not None and not user.is_bot:
            session: AsyncSession = data["session"]
            await self._maybe_award_ghost(session, user.id)
            # messages_count увеличиваем только на сообщения, не на кнопки.
            await users_repo.upsert_user(
                session,
                user.id,
                user.username,
                user.first_name,
                touch_activity=True,
                increment_messages=is_message,
            )
            if is_message:
                # Дневной счётчик (день — по часовому поясу Europe/Amsterdam).
                await messages_repo.increment_daily(
                    session, user.id, now_local().date()
                )
        return await handler(event, data)

    async def _maybe_award_ghost(self, session: AsyncSession, user_id: int) -> None:
        """Тихо выдаёт «Призрак Возни» при возвращении после долгого молчания."""
        try:
            existing = await users_repo.get_user(session, user_id)
            if existing is None or existing.last_active_at is None:
                return
            gap = now_utc() - existing.last_active_at
            if gap >= timedelta(days=balance.GHOST_RETURN_DAYS):
                # Локальный импорт во избежание циклов на старте приложения.
                from app.features.achievements.service import award_specific

                await award_specific(session, user_id, "ghost")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ghost-achievement check failed: %s", exc)
