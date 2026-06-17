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
KEY_FAST_MODEL = "fast_model"  # дешёвая/быстрая модель для служебных задач
KEY_MODELS_BY_ROLE = "models_by_role"  # JSON: роль→модель (мульти-модельность)
KEY_TEMPERATURE = "temperature"
KEY_MAX_TOKENS = "max_tokens"
KEY_POSTS_PER_DAY = "posts_per_day_max"
KEY_MIN_SEVERITY = "min_severity"
KEY_AUTONOMOUS_ENABLED = "autonomous_enabled"  # сам по себе постит в чат (off по умолч.)
# Реактивный режим (ответы в чате).
KEY_REPLY_ENABLED = "reply_enabled"          # отвечать ли на обращения в чате
KEY_REPLY_COOLDOWN = "reply_cooldown_sec"    # анти-спам: пауза между ответами
KEY_NAME_TRIGGERS = "name_triggers"          # слова-обращения (по имени)
KEY_RANDOM_CHANCE = "random_butt_in_chance"  # шанс случайного встревания (0..1)
# Экономическая власть друна (налог/подачка). Жёсткие предохранители.
KEY_ECON_ENABLED = "econ_enabled"            # включена ли власть над ешками
KEY_ECON_MAX_PCT = "econ_max_pct"            # макс. доля баланса за операцию (0..1)
KEY_ECON_MAX_ABS = "econ_max_abs"            # макс. абсолют ешек за операцию
KEY_ECON_COOLDOWN_SEC = "econ_cooldown_sec"  # пауза между операциями над одним игроком
KEY_ECON_DAILY_CAP = "econ_daily_cap"        # макс. операций в день на весь чат
# Веб-доступ (#11): друн может искать в интернете на запрос. Жёстко off по умолч.
KEY_WEB_ENABLED = "web_enabled"
KEY_WEB_SEARCH_URL = "web_search_url"        # endpoint поиска (SearXNG/совместимый JSON)
KEY_WEB_DAILY_CAP = "web_daily_cap"          # макс. веб-запросов в сутки
# Генерация картинок (#10): друн может рисовать. Жёстко off по умолчанию.
KEY_IMAGE_ENABLED = "image_enabled"
KEY_IMAGE_BASE_URL = "image_base_url"        # endpoint генерации (OpenAI images-совместимый)
KEY_IMAGE_API_KEY = "image_api_key"          # ключ (если пуст — берём api_key)
KEY_IMAGE_MODEL = "image_model"              # модель генерации
KEY_IMAGE_DAILY_CAP = "image_daily_cap"      # макс. картинок в сутки

# Дефолты (БД переопределяет). base_url пустой → OpenAI по умолчанию в провайдере.
DEFAULTS: dict[str, Any] = {
    KEY_ENABLED: False,
    KEY_BASE_URL: "https://api.openai.com/v1",
    KEY_API_KEY: "",
    KEY_MODEL: "gpt-4o-mini",
    KEY_FAST_MODEL: "",
    KEY_MODELS_BY_ROLE: {},
    KEY_TEMPERATURE: 0.9,
    KEY_MAX_TOKENS: 600,
    KEY_POSTS_PER_DAY: 6,
    KEY_MIN_SEVERITY: 2,
    KEY_AUTONOMOUS_ENABLED: False,
    KEY_REPLY_ENABLED: True,
    KEY_REPLY_COOLDOWN: 20,
    KEY_NAME_TRIGGERS: ["друн", "drun"],
    KEY_RANDOM_CHANCE: 0.03,
    KEY_ECON_ENABLED: False,
    KEY_ECON_MAX_PCT: 0.05,
    KEY_ECON_MAX_ABS: 1000,
    KEY_ECON_COOLDOWN_SEC: 7200,
    KEY_ECON_DAILY_CAP: 20,
    KEY_WEB_ENABLED: False,
    KEY_WEB_SEARCH_URL: "",
    KEY_WEB_DAILY_CAP: 50,
    KEY_IMAGE_ENABLED: False,
    KEY_IMAGE_BASE_URL: "",
    KEY_IMAGE_API_KEY: "",
    KEY_IMAGE_MODEL: "gpt-image-1",
    KEY_IMAGE_DAILY_CAP: 20,
}

# Имена промптов.
PROMPT_PERSONA = "persona"        # кто такой друн (голос) — обычно копия ПЕРСОНАЖ.txt
PROMPT_WORLD = "world"            # лор мира — обычно копия МИР.txt
PROMPT_OBSERVATION = "observation"  # инструкция для одиночного наблюдения
PROMPT_REACTION = "reaction"      # инструкция для реакции на событие
PROMPT_REPLY = "reply"            # инструкция для ответа на обращение в чате


# --- Мульти-модельность: специализированные роли ------------------------------
# Каждой задаче — оптимальная модель. Дорогие/умные — на голос и нарратив,
# дешёвые/быстрые — на служебную обработку (извлечение/суммаризация фактов).
ROLE_NARRATOR = "narrator"            # голос друна: ответы, реакции, истории
ROLE_MEMORY_EXTRACT = "memory_extract"  # вытащить факты из чата
ROLE_MEMORY_SUMMARY = "memory_summary"  # портреты/сжатие памяти
ROLE_EVENT_ANALYSIS = "event_analysis"  # анализ событий мира
ROLE_PLANNING = "planning"            # парсинг owner-команд в tool-вызовы
ROLE_VISION = "vision"                # понимание изображений
ROLE_MODERATION = "moderation"        # модерационные рассуждения

ALL_ROLES = (
    ROLE_NARRATOR, ROLE_MEMORY_EXTRACT, ROLE_MEMORY_SUMMARY, ROLE_EVENT_ANALYSIS,
    ROLE_PLANNING, ROLE_VISION, ROLE_MODERATION,
)

# Дефолтная раскладка ролей по доступным моделям. ВНИМАНИЕ: это СПРАВОЧНАЯ
# таблица-рекомендация, которую НЕ читает model_for() (тот специально не
# подставляет имена моделей, которых может не быть на endpoint). Её показывает
# админка как «пресет одним кликом»: оператор копирует нужные строки в БД-ключ
# models_by_role, и только тогда раскладка вступает в силу. Имена моделей здесь
# — пожелания, а не гарантия доступности у провайдера.
# ВНИМАНИЕ: дублируется в v0-voznya/app/admin/ai/ai-manager.tsx
# (RECOMMENDED_ROLE_MODELS) — при правке моделей синхронизируй оба места.
DEFAULT_ROLE_MODELS: dict[str, str] = {
    ROLE_NARRATOR: "claude-opus-4-8",        # живой голос — самая сильная
    ROLE_MEMORY_EXTRACT: "gpt-5.4-mini",     # дёшево и быстро, много вызовов
    ROLE_MEMORY_SUMMARY: "claude-haiku-4-5", # компактные портреты
    ROLE_EVENT_ANALYSIS: "gpt-5.4-mini",     # анализ событий
    ROLE_PLANNING: "gpt-5.4",                # точный разбор команд в JSON
    ROLE_VISION: "gpt-5.5",                  # мультимодальность (надёжно, RU)
    ROLE_MODERATION: "claude-sonnet-4-6",    # взвешенные модерац-решения
}


@dataclass
class AiConfig:
    """Снимок конфигурации провайдера для одного запроса."""

    enabled: bool
    base_url: str
    api_key: str
    model: str
    fast_model: str
    models_by_role: dict[str, str]
    temperature: float
    max_tokens: int
    posts_per_day_max: int
    min_severity: int
    autonomous_enabled: bool
    reply_enabled: bool
    reply_cooldown_sec: int
    name_triggers: list[str]
    random_butt_in_chance: float
    econ_enabled: bool
    econ_max_pct: float
    econ_max_abs: int
    econ_cooldown_sec: int
    econ_daily_cap: int

    # Веб-доступ (#11) и генерация картинок (#10) — опциональны, off по умолчанию.
    web_enabled: bool = False
    web_search_url: str = ""
    web_daily_cap: int = 50
    image_enabled: bool = False
    image_base_url: str = ""
    image_api_key: str = ""
    image_model: str = ""
    image_daily_cap: int = 20

    @property
    def usable(self) -> bool:
        """Можно ли реально дёргать модель (включено и есть ключ)."""
        return bool(self.enabled and self.api_key and self.model)

    @property
    def web_usable(self) -> bool:
        """Веб-поиск доступен (включён и задан endpoint)."""
        return bool(self.web_enabled and self.web_search_url)

    @property
    def image_usable(self) -> bool:
        """Генерация картинок доступна (включена, есть endpoint/модель/ключ)."""
        key = self.image_api_key or self.api_key
        return bool(
            self.image_enabled and self.image_base_url and self.image_model and key
        )

    def model_for(self, role: str) -> str:
        """Модель для конкретной роли (мульти-модельность).

        Безопасные дефолты: пока оператор НЕ задал ``models_by_role`` в БД, мы
        сохраняем текущее поведение — служебные роли идут на ``fast_model``
        (если задана), голосовые/планировочные — на основную ``model``. Так мы
        не ломаем работу, подставляя имена моделей, которых нет на endpoint.
        Рекомендованную раскладку (DEFAULT_ROLE_MODELS) оператор видит в админке
        и включает явно.
        """
        explicit = (self.models_by_role or {}).get(role, "")
        if explicit:
            return explicit
        # Без явной настройки: дешёвые служебные роли → fast_model, если есть.
        cheap_roles = {
            ROLE_MEMORY_EXTRACT, ROLE_MEMORY_SUMMARY, ROLE_EVENT_ANALYSIS,
        }
        if role in cheap_roles and self.fast_model:
            return self.fast_model
        return self.model


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

    def _as_str_list(v: Any, d: list[str]) -> list[str]:
        if isinstance(v, list):
            out = [str(x).strip().lower() for x in v if str(x).strip()]
            return out or d
        if isinstance(v, str):
            out = [p.strip().lower() for p in v.split(",") if p.strip()]
            return out or d
        return d

    def _as_role_map(v: Any) -> dict[str, str]:
        """Парсит models_by_role: только известные роли, значения — строки."""
        if not isinstance(v, dict):
            return {}
        out: dict[str, str] = {}
        for role, model in v.items():
            r = str(role).strip().lower()
            m = str(model).strip()
            if r in ALL_ROLES and m:
                out[r] = m
        return out

    return AiConfig(
        enabled=_as_bool(_g(KEY_ENABLED)),
        base_url=str(_g(KEY_BASE_URL) or DEFAULTS[KEY_BASE_URL]).rstrip("/"),
        api_key=str(_g(KEY_API_KEY) or ""),
        model=str(_g(KEY_MODEL) or DEFAULTS[KEY_MODEL]),
        fast_model=str(_g(KEY_FAST_MODEL) or ""),
        models_by_role=_as_role_map(_g(KEY_MODELS_BY_ROLE)),
        temperature=_as_float(_g(KEY_TEMPERATURE), DEFAULTS[KEY_TEMPERATURE]),
        max_tokens=_as_int(_g(KEY_MAX_TOKENS), DEFAULTS[KEY_MAX_TOKENS]),
        posts_per_day_max=_as_int(_g(KEY_POSTS_PER_DAY), DEFAULTS[KEY_POSTS_PER_DAY]),
        min_severity=_as_int(_g(KEY_MIN_SEVERITY), DEFAULTS[KEY_MIN_SEVERITY]),
        autonomous_enabled=_as_bool(_g(KEY_AUTONOMOUS_ENABLED)),
        reply_enabled=_as_bool(_g(KEY_REPLY_ENABLED)),
        reply_cooldown_sec=_as_int(_g(KEY_REPLY_COOLDOWN), DEFAULTS[KEY_REPLY_COOLDOWN]),
        name_triggers=_as_str_list(_g(KEY_NAME_TRIGGERS), DEFAULTS[KEY_NAME_TRIGGERS]),
        random_butt_in_chance=_as_float(
            _g(KEY_RANDOM_CHANCE), DEFAULTS[KEY_RANDOM_CHANCE]
        ),
        econ_enabled=_as_bool(_g(KEY_ECON_ENABLED)),
        econ_max_pct=_as_float(_g(KEY_ECON_MAX_PCT), DEFAULTS[KEY_ECON_MAX_PCT]),
        econ_max_abs=_as_int(_g(KEY_ECON_MAX_ABS), DEFAULTS[KEY_ECON_MAX_ABS]),
        econ_cooldown_sec=_as_int(
            _g(KEY_ECON_COOLDOWN_SEC), DEFAULTS[KEY_ECON_COOLDOWN_SEC]
        ),
        econ_daily_cap=_as_int(_g(KEY_ECON_DAILY_CAP), DEFAULTS[KEY_ECON_DAILY_CAP]),
        web_enabled=_as_bool(_g(KEY_WEB_ENABLED)),
        web_search_url=str(_g(KEY_WEB_SEARCH_URL) or "").rstrip("/"),
        web_daily_cap=_as_int(_g(KEY_WEB_DAILY_CAP), DEFAULTS[KEY_WEB_DAILY_CAP]),
        image_enabled=_as_bool(_g(KEY_IMAGE_ENABLED)),
        image_base_url=str(_g(KEY_IMAGE_BASE_URL) or "").rstrip("/"),
        image_api_key=str(_g(KEY_IMAGE_API_KEY) or ""),
        image_model=str(_g(KEY_IMAGE_MODEL) or DEFAULTS[KEY_IMAGE_MODEL]),
        image_daily_cap=_as_int(_g(KEY_IMAGE_DAILY_CAP), DEFAULTS[KEY_IMAGE_DAILY_CAP]),
    )


async def get_prompt(session: AsyncSession, name: str, default: str = "") -> str:
    """Возвращает тело промпта по имени (или ``default``, если не задан)."""
    await _ensure_loaded(session)
    return _prompts_cache.get(name, default)
