"""Динамические настройки из БД (Admin V2, Этап 9).

Позволяет переопределять числовые параметры (цены/веса/шансы/кулдауны) из
админки БЕЗ деплоя и миграций. Источник истины — по-прежнему код
``app/settings/balance.py``; таблица ``app_settings`` лишь ПЕРЕОПРЕДЕЛЯЕТ
отдельные ключи. Если ключа нет в БД или БД недоступна — возвращается дефолт.

Кэш: процессный словарь с TTL (по умолчанию 60 с), чтобы не бить в БД на каждый
вызов. После правки из админки изменения подхватятся не позже TTL (или сразу,
если вызвать :func:`invalidate_cache`).

Использование (в хендлере/сервисе, где есть AsyncSession):

    from app.settings import dynamic
    max_bet = await dynamic.get_int(session, "casino.max_bet", balance.CASINO_MAX_BET)

Ключи — стабильные строки вида ``<категория>.<имя>``. Категория используется для
группировки в админ-UI.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.models import AppSetting

logger = get_logger(__name__)

# TTL кэша в секундах. Можно поднять/опустить — это лишь про свежесть правок.
_CACHE_TTL_SECONDS = 60.0

# Процессный кэш: {key: value}. Заполняется целиком одним запросом.
_cache: dict[str, Any] = {}
_cache_loaded_at: float = 0.0


def invalidate_cache() -> None:
    """Сбрасывает кэш — следующий доступ перечитает БД. Зовётся после правки."""
    global _cache_loaded_at
    _cache_loaded_at = 0.0


async def _ensure_loaded(session: AsyncSession) -> None:
    """Загружает все настройки в кэш, если он устарел. Best-effort."""
    global _cache, _cache_loaded_at
    now = time.monotonic()
    if now - _cache_loaded_at < _CACHE_TTL_SECONDS and _cache_loaded_at > 0:
        return
    try:
        rows = await session.execute(select(AppSetting.key, AppSetting.value))
        _cache = {key: value for key, value in rows}
        _cache_loaded_at = now
    except Exception:  # noqa: BLE001
        # БД недоступна/таблицы ещё нет — работаем на дефолтах кода.
        logger.debug("app_settings load failed; using code defaults", exc_info=True)
        # НЕ обнуляем _cache: пусть остаётся прошлый успешный снимок, если был.
        if _cache_loaded_at == 0.0:
            _cache = {}


async def get_value(session: AsyncSession, key: str, default: Any) -> Any:
    """Возвращает значение настройки или ``default`` (если ключа нет)."""
    await _ensure_loaded(session)
    return _cache.get(key, default)


async def get_int(session: AsyncSession, key: str, default: int) -> int:
    """Целочисленная настройка с фолбэком на дефолт при некорректном значении."""
    raw = await get_value(session, key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("app_setting %s is not int: %r; using default", key, raw)
        return default


async def get_float(session: AsyncSession, key: str, default: float) -> float:
    """Дробная настройка с фолбэком на дефолт при некорректном значении."""
    raw = await get_value(session, key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("app_setting %s is not float: %r; using default", key, raw)
        return default


async def get_bool(session: AsyncSession, key: str, default: bool) -> bool:
    """Булева настройка. Принимает true/false, 1/0, "true"/"false"."""
    raw = await get_value(session, key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return default
