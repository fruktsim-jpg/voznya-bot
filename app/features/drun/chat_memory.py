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

from sqlalchemy import delete, or_, select
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
    # Имя может быть неуникальным (несколько игроков с одинаковым ником в окне).
    # Копим МНОЖЕСТВО id на имя, чтобы при коллизии не приписать факт не тому.
    name_to_ids: dict[str, set[int]] = {}
    for m in msgs:
        nm = (m.meta or {}).get("name") or f"id{m.user_id}"
        lines.append(f"{nm}: {m.content}")
        if m.user_id:
            name_to_ids.setdefault(nm.lower(), set()).add(m.user_id)

    log = "\n".join(lines)
    user_msg = (
        f"{_INSTRUCTION.format(max=_MAX_FACTS)}\n\n# ЛОГ ЧАТА\n{log}"
    )
    try:
        raw = await drun_provider.chat(
            cfg, system=_SYSTEM, messages=[{"role": "user", "content": user_msg}],
            model=cfg.model_for(drun_config.ROLE_MEMORY_EXTRACT),
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
        ids = name_to_ids.get(item["name"].lower(), set())
        # Однозначное имя → привязываем к игроку; коллизия или неизвестное имя →
        # храним как факт про мир/чат (subject_id=None), но НЕ приписываем
        # конкретному человеку, чтобы не путать досье.
        subject_id = next(iter(ids)) if len(ids) == 1 else None
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
    """Уже сохранённые НЕ протухшие (subject_id, fact) — грубая дедупликация.

    Ограничиваем выборку живыми записями (``expires_at`` пуст или в будущем),
    чтобы дедуп-сет не рос вместе с накопленной протухшей памятью.
    """
    now = now_utc()
    rows = (
        await session.execute(
            select(AiMemory.subject_id, AiMemory.fact).where(
                or_(AiMemory.expires_at.is_(None), AiMemory.expires_at > now)
            )
        )
    ).all()
    return {(r[0], r[1]) for r in rows}


async def purge_expired(session: AsyncSession) -> int:
    """Физически удаляет протухшие факты (``expires_at`` в прошлом).

    Без этого таблица ``ai_memories`` только растёт: read-фильтр прячет
    протухшее, но не освобождает место. Возвращает число удалённых строк.
    """
    now = now_utc()
    result = await session.execute(
        delete(AiMemory).where(
            AiMemory.expires_at.is_not(None), AiMemory.expires_at <= now
        )
    )
    return int(result.rowcount or 0)


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
                removed = await purge_expired(session)
                n = await distill_chat(session)
                await session.commit()
                if n or removed:
                    logger.info(
                        "drun chat memory: +%d facts, -%d expired", n, removed
                    )
        except Exception:  # noqa: BLE001
            logger.warning("drun chat distill failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_chat_distill",
        replace_existing=True,
    )
