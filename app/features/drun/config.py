"""Конфиг и промпты Тёмного друна из БД (правятся в админке без рестарта).

``ai_settings`` (key→JSONB) хранит параметры провайдера; ``ai_prompts`` —
именованные промпты. У всего есть дефолты в коде, поэтому при пустой БД друн
имеет вменяемую конфигурацию (но молчит, пока не задан api_key и enabled).

Кэш с TTL по образцу ``app.settings.dynamic`` — чтобы не бить в БД на каждый
запрос; после правки в админке изменения подхватятся не позже TTL или сразу
через :func:`invalidate_cache`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.models import AiPrompt, AiSetting

logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 30.0

# Ключи ai_settings.
KEY_ENABLED = "enabled"
KEY_BASE_URL = "base_url"
KEY_API_KEY = "api_key"
KEY_MODEL = "model"
KEY_TEMPERATURE = "temperature"
KEY_MAX_TOKENS = "max_tokens"
KEY_POSTS_PER_DAY = "posts_per_day_max"
KEY_MIN_SEVERITY = "min_severity"

# Дефолты (БД переопределяет). base_url пустой → OpenAI по умолчанию в провайдере.
DEFAULTS: dict[str, Any] = {
    KEY_ENABLED: False,
    KEY_BASE_URL: "https://api.openai.com/v1",
    KEY_API_KEY: "",
    KEY_MODEL: "gpt-4o-mini",
    KEY_TEMPERATURE: 0.9,
    KEY_MAX_TOKENS: 320,
    KEY_POSTS_PER_DAY: 6,
    KEY_MIN_SEVERITY: 2,
}

# Имена промптов.
PROMPT_PERSONA = "persona"        # кто такой друн (голос) — обычно копия ПЕРСОНАЖ.txt
PROMPT_WORLD = "world"            # лор мира — обычно копия МИР.txt
PROMPT_OBSERVATION = "observation"  # инструкция для одиночного наблюдения
PROMPT_REACTION = "reaction"      # инструкция для реакции на событие


@dataclass
class AiConfig:
    """Снимок конфигурации провайдера для одного запроса."""

    enabled: bool
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    posts_per_day_max: int
    min_severity: int

    @property
    def usable(self) -> bool:
        """Можно ли реально дёргать модель (включено и есть ключ)."""
        return bool(self.enabled and self.api_key and self.model)


_settings_cache: dict[str, Any] = {}
_prompts_cache: dict[str, str] = {}
_loaded_at: float = 0.0


def invalidate_cache() -> None:
    """Сбрасывает кэш — следующий доступ перечитает БД (зовётся после правки)."""
    global _loaded_at
    _loaded_at = 0.0


async def _ensure_loaded(session: AsyncSession) -> None:
    global _settings_cache, _prompts_cache, _loaded_at
    now = time.monotonic()
    if _loaded_at > 0 and now - _loaded_at < _CACHE_TTL_SECONDS:
        return
    try:
        srows = await session.execute(select(AiSetting.key, AiSetting.value))
        _settings_cache = {k: v for k, v in srows}
        prows = await session.execute(
            select(AiPrompt.name, AiPrompt.body).where(AiPrompt.enabled.is_(True))
        )
        _prompts_cache = {n: b for n, b in prows}
        _loaded_at = now
    except Exception:  # noqa: BLE001
        logger.debug("ai_settings/ai_prompts load failed; using defaults", exc_info=True)
        if _loaded_at == 0.0:
            _settings_cache, _prompts_cache = {}, {}


async def get_config(session: AsyncSession) -> AiConfig:
    """Возвращает текущую конфигурацию провайдера (БД поверх дефолтов)."""
    await _ensure_loaded(session)

    def _g(key: str) -> Any:
        return _settings_cache.get(key, DEFAULTS[key])

    def _as_bool(v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return False

    def _as_float(v: Any, d: float) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return d

    def _as_int(v: Any, d: int) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return d

    return AiConfig(
        enabled=_as_bool(_g(KEY_ENABLED)),
        base_url=str(_g(KEY_BASE_URL) or DEFAULTS[KEY_BASE_URL]).rstrip("/"),
        api_key=str(_g(KEY_API_KEY) or ""),
        model=str(_g(KEY_MODEL) or DEFAULTS[KEY_MODEL]),
        temperature=_as_float(_g(KEY_TEMPERATURE), DEFAULTS[KEY_TEMPERATURE]),
        max_tokens=_as_int(_g(KEY_MAX_TOKENS), DEFAULTS[KEY_MAX_TOKENS]),
        posts_per_day_max=_as_int(_g(KEY_POSTS_PER_DAY), DEFAULTS[KEY_POSTS_PER_DAY]),
        min_severity=_as_int(_g(KEY_MIN_SEVERITY), DEFAULTS[KEY_MIN_SEVERITY]),
    )


async def get_prompt(session: AsyncSession, name: str, default: str = "") -> str:
    """Возвращает тело промпта по имени (или ``default``, если не задан)."""
    await _ensure_loaded(session)
    return _prompts_cache.get(name, default)
