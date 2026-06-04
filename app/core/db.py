"""Подключение к базе данных и управление сессиями.

Используется асинхронный движок SQLAlchemy поверх asyncpg.
Сессии раздаются через ``async_sessionmaker`` и инъектируются в хендлеры
через middleware (см. :mod:`app.middlewares`).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Возвращает (лениво создавая) асинхронный движок БД."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Возвращает фабрику асинхронных сессий."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """Закрывает пул соединений (при остановке приложения)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
