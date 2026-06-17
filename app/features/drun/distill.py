"""Дистилляция долгосрочной памяти друна из событий мира.

Дёшево (без LLM) превращаем ``world_events`` в устойчивые факты об игроках и их
взаимодействиях, которые потом подмешиваются в контекст:

* по игроку: «X выиграл N дуэлей», «X сорвал большой куш в казино»;
* взаимодействия/соперничества: «X и Y часто рубятся в дуэлях» (по повторам);
* браки: «X в браке с Y».

Идемпотентно: факт привязан к ``subject_id`` + текстовому ключу; перед вставкой
проверяем, нет ли уже такого (по ``kind`` и совпадению префикса). Имена —
человекочитаемые (через names.resolve_names), без сырых id.

Запускается планировщиком раз в N минут. Любой сбой — тихий лог, мир не падает.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.features.drun import memory as drun_memory
from app.features.drun.names import name_for, resolve_names
from app.models import AiMemory, Marriage, User, WorldEvent

logger = get_logger(__name__)

# Сколько последних событий перевариваем за один проход.
_SCAN_LIMIT = 500
# Порог «соперничества»: столько дуэлей между парой, чтобы запомнить вражду.
_RIVALRY_MIN = 3


async def _existing_facts(session: AsyncSession) -> set[tuple[int | None, str]]:
    """Множество уже сохранённых (subject_id, kind) для грубой дедупликации."""
    rows = (
        await session.execute(select(AiMemory.subject_id, AiMemory.kind, AiMemory.fact))
    ).all()
    return {(r[0], r[2]) for r in rows}


async def distill(session: AsyncSession) -> int:
    """Один проход дистилляции. Возвращает число добавленных фактов."""
    events = (
        await session.execute(
            select(WorldEvent)
            .order_by(WorldEvent.created_at.desc())
            .limit(_SCAN_LIMIT)
        )
    ).scalars().all()
    if not events:
        return 0

    ids: set[int] = set()
    for e in events:
        if e.actor_id:
            ids.add(e.actor_id)
        if e.target_id:
            ids.add(e.target_id)
    names = await resolve_names(session, ids)

    # --- Агрегаты по игрокам и парам ----------------------------------------
    duel_wins: Counter[int] = Counter()
    pair_duels: Counter[tuple[int, int]] = Counter()
    big_casino: set[int] = set()
    for e in events:
        if e.type == "duel_won" and e.actor_id:
            duel_wins[e.actor_id] += 1
            if e.target_id:
                pair = tuple(sorted((e.actor_id, e.target_id)))
                pair_duels[pair] += 1
        elif e.type == "casino_big_win" and e.actor_id:
            big_casino.add(e.actor_id)

    existing = await _existing_facts(session)
    added = 0

    def _add(subject_id: int | None, kind: str, fact: str, weight: int) -> None:
        nonlocal added
        if (subject_id, fact) in existing:
            return
        existing.add((subject_id, fact))
        session.add(
            AiMemory(
                subject_id=subject_id,
                kind=kind,
                fact=fact,
                weight=weight,
                source="auto",
            )
        )
        added += 1

    # Бойцы: заметные по числу побед.
    for uid, wins in duel_wins.items():
        if wins >= 3:
            _add(uid, "trait", f"{name_for(names, uid)} — задира, часто побеждает в дуэлях", 2)

    # Казино-везунчики.
    for uid in big_casino:
        _add(uid, "milestone", f"{name_for(names, uid)} срывал большой куш в казино", 2)

    # Соперничества (пара часто рубится).
    for (a, b), cnt in pair_duels.items():
        if cnt >= _RIVALRY_MIN:
            fact = f"{name_for(names, a)} и {name_for(names, b)} — заклятые соперники по дуэлям"
            _add(None, "rivalry", fact, 3)

    # Браки (актуальные).
    marriages = (
        await session.execute(select(Marriage).where(Marriage.divorced_at.is_(None)))
    ).scalars().all()
    if marriages:
        mids: set[int] = set()
        for m in marriages:
            mids.update((m.user_id_1, m.user_id_2))
        mnames = await resolve_names(session, mids)
        for m in marriages:
            fact = f"{name_for(mnames, m.user_id_1)} в браке с {name_for(mnames, m.user_id_2)}"
            _add(None, "fact", fact, 2)

    if added:
        # Фиксируем в сессии, чтобы повторный проход (и параллельные читатели в
        # той же сессии) уже видели факты и не плодили дубли.
        await session.flush()
    return added


def setup_memory_distill(
    scheduler,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    minutes: int = 30,
) -> None:
    """Регистрирует периодическую дистилляцию памяти в планировщике."""

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                n = await distill(session)
                await session.commit()
                if n:
                    logger.info("drun memory: +%d facts", n)
        except Exception:  # noqa: BLE001
            logger.warning("drun memory distill failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_memory_distill",
        replace_existing=True,
    )
