"""Middleware, выдающий сессию БД на каждый апдейт.

Открывает одну сессию на обработку апдейта, кладёт её в ``data["session"]``
и фиксирует (commit) при успешной обработке либо откатывает (rollback)
при исключении. Благодаря единой сессии хендлеры и сервисы работают в одной
транзакции, что позволяет безопасно использовать блокировки строк.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DbSessionMiddleware(BaseMiddleware):
    """Инъекция сессии БД и управление транзакцией."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.sessionmaker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
