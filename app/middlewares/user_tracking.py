"""Middleware регистрации и трекинга активности пользователей.

На каждое сообщение от реального пользователя обновляет его запись в БД:
username, имя и время последней активности. Это формирует пул «активных»
участников для номинаций (Пидор/Пара дня).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import users as users_repo


class UserTrackingMiddleware(BaseMiddleware):
    """Апсертит пользователя и отмечает его активность.

    Активность фиксируется как для сообщений, так и для нажатий на кнопки.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user

        if user is not None and not user.is_bot:
            session: AsyncSession = data["session"]
            await users_repo.upsert_user(
                session,
                user.id,
                user.username,
                user.first_name,
                touch_activity=True,
            )
        return await handler(event, data)
