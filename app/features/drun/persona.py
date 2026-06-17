"""Сборка системного промпта друна: голос (ПЕРСОНАЖ.txt) + мир (МИР.txt).

Приоритет источников:
1. Промпты из БД (``ai_prompts`` persona/world) — правятся в админке.
2. Фолбэк — файлы лора в корне репозитория бота (``ПЕРСОНАЖ.txt``/``МИР.txt``).
3. Жёсткий минимальный фолбэк в коде, если ни того, ни другого нет.

Так друн всегда говорит «по-вознячьи», даже до первой настройки в админке.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.features.drun import config as drun_config

logger = get_logger(__name__)

# Корень репозитория бота: app/features/drun/persona.py → ../../../
_REPO_ROOT = Path(__file__).resolve().parents[3]

_FALLBACK_PERSONA = (
    "Ты — Тёмный друн, внутриигровой наблюдатель мира Возни. Сельско-городской "
    "стиль, нейтрально-агрессивный, мемный. Говоришь «шо» вместо «что». Никаких "
    "корпоративных фраз («Уважаемый пользователь», «Операция выполнена»). Ты "
    "комментируешь происходящее, а не помогаешь как ассистент."
)


@lru_cache(maxsize=4)
def _read_lore_file(filename: str) -> str:
    """Читает файл лора из корня репозитория (кэшируется). Пусто, если нет."""
    path = _REPO_ROOT / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.debug("lore file not found: %s", path)
        return ""


async def build_system_prompt(session: AsyncSession) -> str:
    """Собирает системный промпт: персона + мир + жёсткие правила вывода."""
    persona = await drun_config.get_prompt(
        session, drun_config.PROMPT_PERSONA, ""
    ) or _read_lore_file("ПЕРСОНАЖ.txt") or _FALLBACK_PERSONA
    world = await drun_config.get_prompt(
        session, drun_config.PROMPT_WORLD, ""
    ) or _read_lore_file("МИР.txt")

    parts = [persona]
    if world:
        parts.append("# ЛОР МИРА (используй как контекст, не пересказывай)\n" + world)
    parts.append(
        "# ПРАВИЛА\n"
        "- Ты наблюдатель и комментатор, не ассистент. Не предлагай помощь.\n"
        "- Опирайся на данные ниже (статистика, события). Не выдумывай цифры.\n"
        "- Коротко: 1–3 фразы. Будь в образе всегда.\n"
        "- Запрещены официальные/корпоративные формулировки."
    )
    return "\n\n".join(parts)
