"""Обёртка над APScheduler для фоновых задач.

Используется для:
* суточного планирования появления кладов;
* отложенного удаления сообщений (чистота чата);
* периодических служебных задач.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """Возвращает (лениво создавая) глобальный планировщик."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=get_settings().timezone)
    return _scheduler


def start_scheduler() -> AsyncIOScheduler:
    """Запускает планировщик, если он ещё не запущен."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
    return scheduler


def shutdown_scheduler() -> None:
    """Останавливает планировщик."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
