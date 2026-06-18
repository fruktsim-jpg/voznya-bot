"""Самообучение друна: рефлексия над чатом → «уроки», влияющие на поведение.

Друн должен СТАНОВИТЬСЯ УМНЕЕ со временем, а не только накапливать факты о
людях. Раз в N часов он смотрит на свежий чат + на свои недавние реплики и
вытаскивает УСТОЙЧИВЫЕ УРОКИ про культуру именно ЭТОГО чата:
* что тут считается смешным, а что — кринж и не заходит;
* местный сленг и его смысл («бурмалда = ...», «67 = знак»);
* как принято общаться, на что люди реагируют тепло/агрессивно;
* что в поведении самого друна заходит, а что бесит людей.

Уроки кладём в ``ai_memories`` (kind='lesson', subject_id=NULL, без TTL — это
долгий капитал) и подмешиваем топ-N в системный промпт. Так промпт фактически
«дообучается» под чат, оставаясь в рамках персоны. Сбой — тихий лог.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun import config as drun_config
from app.features.drun import memory as drun_memory
from app.features.drun import provider as drun_provider
from app.models import AiMemory

logger = get_logger(__name__)

LESSON_KIND = "lesson"
# Сколько свежих реплик чата даём на рефлексию.
_CHAT_WINDOW = 80
# Сколько уроков максимум держим всего (старые слабые вытесняются).
_MAX_LESSONS = 24
# Сколько уроков максимум извлекаем за один проход.
_PER_RUN = 5
# Сколько топ-уроков подмешиваем в системный промпт.
_INJECT_TOP = 8

_SYSTEM = (
    "Ты — саморефлексия Тёмного друна (Меллстрой), живущего в этом чате. Твоя "
    "задача — стать УМНЕЕ: глядя на лог чата и на свои реплики, вынести "
    "несколько УСТОЙЧИВЫХ УРОКОВ про культуру ИМЕННО этого чата, чтобы дальше "
    "общаться точнее и смешнее. Не пересказывай события — формулируй выводы."
)
_INSTRUCTION = (
    "Верни СТРОГО JSON-массив (без пояснений, без ```) до {max} объектов вида "
    '{{"lesson":"короткий вывод-правило на русском","weight":1-3}}. '
    "Каждый урок — это ЗНАНИЕ про чат, полезное на будущее: местный сленг и "
    "его смысл; что тут смешно, а что кринж и не заходит; как люди реагируют "
    "на твои подколы; локальные мемы и традиции; на что в чате принято "
    "реагировать тепло или агрессивно. weight: 3 — важное правило поведения, "
    "1 — мелкое наблюдение. Только то, что устойчиво и пригодится МНОГО раз. "
    "Если нового знания нет — верни []."
)


def _parse_lessons(raw: str) -> list[dict]:
    """Парсит JSON-массив уроков из ответа модели, терпимо к мусору."""
    text = (raw or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for el in data[:_PER_RUN]:
        if not isinstance(el, dict):
            continue
        lesson = str(el.get("lesson", "")).strip()
        if not lesson or len(lesson) > 220:
            continue
        try:
            weight = int(el.get("weight", 1))
        except (TypeError, ValueError):
            weight = 1
        out.append({"lesson": lesson, "weight": max(1, min(3, weight))})
    return out


async def _existing_lessons(session: AsyncSession) -> dict[str, AiMemory]:
    """Текущие уроки: нормализованный текст → запись (для дедупа/усиления)."""
    rows = (
        await session.execute(
            select(AiMemory).where(AiMemory.kind == LESSON_KIND)
        )
    ).scalars().all()
    return {m.fact.strip().lower(): m for m in rows}


async def _prune_lessons(session: AsyncSession) -> None:
    """Держим не больше _MAX_LESSONS: вытесняем самые слабые и старые."""
    rows = (
        await session.execute(
            select(AiMemory)
            .where(AiMemory.kind == LESSON_KIND)
            .order_by(AiMemory.weight.desc(), AiMemory.updated_at.desc())
        )
    ).scalars().all()
    for stale in rows[_MAX_LESSONS:]:
        await session.delete(stale)


async def reflect(session: AsyncSession) -> int:
    """Один проход рефлексии: чат → уроки. Возвращает число новых/усиленных."""
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return 0

    msgs = await drun_memory.recent_chat(session, channel="chat", limit=_CHAT_WINDOW)
    if len(msgs) < 15:  # мало материала — рефлексировать не на чем
        return 0

    lines = []
    for m in msgs:
        nm = (m.meta or {}).get("name") or f"id{m.user_id}"
        lines.append(f"{nm}: {m.content}")
    # Свои недавние реплики — чтобы учиться на своём заходе/провале.
    try:
        own = await drun_memory.recent_self_posts(session, channel="chat", limit=10)
    except Exception:  # noqa: BLE001
        own = []
    own_block = ("\n# ТВОИ НЕДАВНИЕ РЕПЛИКИ\n" + "\n".join(f"- {p}" for p in own)) if own else ""

    user_msg = (
        f"{_INSTRUCTION.format(max=_PER_RUN)}\n\n# ЛОГ ЧАТА\n"
        + "\n".join(lines)
        + own_block
    )
    try:
        raw = await drun_provider.chat(
            cfg, system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            model=cfg.model_for(drun_config.ROLE_MEMORY_SUMMARY),
        )
    except drun_provider.LlmError as exc:
        logger.debug("reflect llm failed: %s", exc)
        return 0

    lessons = _parse_lessons(raw)
    if not lessons:
        return 0

    existing = await _existing_lessons(session)
    changed = 0
    for item in lessons:
        key = item["lesson"].strip().lower()
        if key in existing:
            # Урок подтверждён повторно — усиливаем вес (макс 3) и ОБНОВЛЯЕМ ts,
            # даже если вес уже на потолке: иначе часто подтверждаемый, но
            # «насыщенный» урок выглядит старым для пруунинга/ранжирования.
            mem = existing[key]
            new_w = min(3, int(mem.weight or 1) + 1)
            mem.updated_at = now_utc()
            if new_w != mem.weight:
                mem.weight = new_w
            changed += 1
        else:
            mem = AiMemory(
                subject_id=None, kind=LESSON_KIND, fact=item["lesson"],
                weight=item["weight"], source="reflect",
            )
            session.add(mem)
            existing[key] = mem
            changed += 1

    await _prune_lessons(session)
    if changed:
        await session.flush()
    return changed


async def top_lessons(session: AsyncSession, limit: int = _INJECT_TOP) -> list[str]:
    """Топ уроков для инжекта в системный промпт (по весу, затем свежести)."""
    try:
        rows = (
            await session.execute(
                select(AiMemory.fact)
                .where(AiMemory.kind == LESSON_KIND)
                .order_by(AiMemory.weight.desc(), AiMemory.updated_at.desc())
                .limit(limit)
            )
        ).all()
        return [r[0] for r in rows if r[0]]
    except Exception:  # noqa: BLE001
        logger.debug("top_lessons failed", exc_info=True)
        return []


def setup_reflection(
    scheduler,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    hours: int = 6,
) -> None:
    """Регистрирует периодическую рефлексию (самообучение) друна."""

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                n = await reflect(session)
                await session.commit()
                if n:
                    logger.info("drun reflect: %d lessons learned/reinforced", n)
        except Exception:  # noqa: BLE001
            logger.warning("drun reflection failed", exc_info=True)

    scheduler.add_job(
        _job, "interval", hours=hours, id="drun_reflect", replace_existing=True,
    )
