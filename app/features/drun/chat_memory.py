"""LLM-дистилляция «живой» памяти из чата: о чём говорят, кто есть кто.

Дешёвая событийная дистилляция (``distill.py``) умеет только статистику дуэлей/
казино/браков. Этого мало — друн не помнит ТЕМЫ разговоров, характеры и
отношения между людьми, поэтому ощущается как бот.

Здесь раз в N минут берём свежую болтовню чата и просим модель вытащить
несколько устойчивых фактов вида «X постоянно ноет про подкрутку», «Y и Z
кореша», «W фанатеет по кейсам». Факты кладём в ``ai_memories`` (source='chat')
с TTL, чтобы старое выветривалось. Сбой — тихий лог, мир не падает.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun import config as drun_config
from app.features.drun import memory as drun_memory
from app.features.drun import provider as drun_provider
from app.models import AiMemory

logger = get_logger(__name__)

# Сколько свежих реплик чата отдаём модели на анализ.
_CHAT_WINDOW = 60
# Сколько фактов максимум просим вернуть за проход.
_MAX_FACTS = 8
# TTL «живых» фактов: разговорное выветривается за неделю, если не подтвердится.
_FACT_TTL_DAYS = 7

_SYSTEM = (
    "Ты — аналитик чата. По логу болтовни выдели УСТОЙЧИВЫЕ наблюдения про "
    "людей: о чём человек постоянно говорит, его характер/манера, отношения и "
    "союзы/конфликты между людьми, привычки в игре. Игнорируй разовый шум, "
    "команды и мусор. Только то, что реально повторяется или ярко характеризует."
)
_INSTRUCTION = (
    "Верни СТРОГО JSON-массив (без пояснений) до {max} объектов вида "
    '{{"name":"ник","fact":"короткий факт о нём на русском","weight":1-3}}. '
    "weight: 1 — мелочь, 2 — заметная черта, 3 — яркая определяющая черта. "
    "Если ничего стоящего нет — верни []. Факт — это про человека, не про "
    "конкретное сообщение. Пиши живым языком, как заметка для себя."
)


async def distill_chat(session: AsyncSession) -> int:
    """Один проход LLM-дистилляции памяти из чата. Возвращает число фактов."""
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return 0

    msgs = await drun_memory.recent_chat(session, channel="chat", limit=_CHAT_WINDOW)
    if len(msgs) < 8:  # мало данных — не дёргаем модель зря
        return 0

    lines = []
    name_to_id: dict[str, int] = {}
    for m in msgs:
        nm = (m.meta or {}).get("name") or f"id{m.user_id}"
        lines.append(f"{nm}: {m.content}")
        if m.user_id:
            name_to_id.setdefault(nm.lower(), m.user_id)

    log = "\n".join(lines)
    user_msg = (
        f"{_INSTRUCTION.format(max=_MAX_FACTS)}\n\n# ЛОГ ЧАТА\n{log}"
    )
    try:
        raw = await drun_provider.chat(
            cfg, system=_SYSTEM, messages=[{"role": "user", "content": user_msg}]
        )
    except drun_provider.LlmError as exc:
        logger.debug("chat distill llm failed: %s", exc)
        return 0

    facts = _parse_facts(raw)
    if not facts:
        return 0

    existing = await _existing_chat_facts(session)
    expires = now_utc() + timedelta(days=_FACT_TTL_DAYS)
    added = 0
    for item in facts:
        fact = item["fact"]
        subject_id = name_to_id.get(item["name"].lower())
        if (subject_id, fact) in existing:
            continue
        existing.add((subject_id, fact))
        session.add(
            AiMemory(
                subject_id=subject_id,
                kind="chat",
                fact=fact,
                weight=item["weight"],
                source="chat",
                expires_at=expires,
            )
        )
        added += 1

    if added:
        await session.flush()
    return added


def _parse_facts(raw: str) -> list[dict]:
    """Парсит JSON-массив фактов из ответа модели, терпимо к мусору."""
    text = (raw or "").strip()
    # выдёргиваем массив, даже если модель обернула его текстом/```
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    out: list[dict] = []
    if not isinstance(data, list):
        return []
    for el in data[:_MAX_FACTS]:
        if not isinstance(el, dict):
            continue
        name = str(el.get("name", "")).strip()
        fact = str(el.get("fact", "")).strip()
        if not name or not fact or len(fact) > 200:
            continue
        try:
            weight = int(el.get("weight", 1))
        except (TypeError, ValueError):
            weight = 1
        weight = max(1, min(3, weight))
        out.append({"name": name, "fact": fact, "weight": weight})
    return out


async def _existing_chat_facts(session: AsyncSession) -> set[tuple[int | None, str]]:
    """Уже сохранённые (subject_id, fact) — грубая дедупликация."""
    rows = (
        await session.execute(select(AiMemory.subject_id, AiMemory.fact))
    ).all()
    return {(r[0], r[1]) for r in rows}


def setup_chat_distill(
    scheduler,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    minutes: int = 45,
) -> None:
    """Регистрирует периодическую LLM-дистилляцию памяти из чата."""

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                n = await distill_chat(session)
                await session.commit()
                if n:
                    logger.info("drun chat memory: +%d facts", n)
        except Exception:  # noqa: BLE001
            logger.warning("drun chat distill failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_chat_distill",
        replace_existing=True,
    )
