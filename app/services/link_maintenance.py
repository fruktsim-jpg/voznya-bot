"""Фоновая чистка протухших запросов привязки (``oidc_link_requests``).

Записи в ``oidc_link_requests`` одноразовые и с TTL: при успешной привязке они
удаляются сразу (``consume_link_request``), но «брошенные» входы (пользователь
начал вход на сайте и не дошёл до бота) остаются. Они безопасны — протухший
токен не создаёт связь, — но без чистки таблица растёт бесконечно.

Сервис запускает периодическое удаление строк с ``expires_at <= now()``,
опираясь на индекс ``ix_oidc_link_requests_expires_at``.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.repositories.account_links import delete_expired_link_requests

logger = get_logger(__name__)

# Как часто подчищать протухшие токены. Раз в час — достаточно: TTL запроса
# 15 минут, объёмы маленькие, нагрузка на индекс пренебрежимо мала.
_CLEANUP_INTERVAL_MINUTES = 60


async def cleanup_expired_link_requests(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> int:
    """Удаляет протухшие запросы привязки. Возвращает число удалённых строк."""
    async with sessionmaker() as session:
        removed = await delete_expired_link_requests(session)
        await session.commit()
    if removed:
        logger.info("Очищено протухших запросов привязки: %s", removed)
    return removed


def setup_link_maintenance(
    scheduler: AsyncIOScheduler,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Регистрирует периодическую чистку протухших запросов привязки."""
    scheduler.add_job(
        cleanup_expired_link_requests,
        trigger="interval",
        minutes=_CLEANUP_INTERVAL_MINUTES,
        args=[sessionmaker],
        id="oidc_link_requests_cleanup",
        replace_existing=True,
        misfire_grace_time=600,
    )
    logger.info(
        "Чистка протухших запросов привязки запланирована (каждые %s мин)",
        _CLEANUP_INTERVAL_MINUTES,
    )
