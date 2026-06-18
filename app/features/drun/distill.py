"""Дистилляция долгосрочной памяти друна из событий мира.

Дёшево (без LLM) превращаем ``world_events`` в устойчивые факты об игроках и их
взаимодействиях, которые потом подмешиваются в контекст:

* по игроку: «X выиграл N дуэлей», «X сорвал большой куш в казино», «X выбивал
  джекпот», «X — кладоискатель / достигатор / любимчик подарков», «X — клиент
  модерации»;
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
    duel_losses: Counter[int] = Counter()
    pair_duels: Counter[tuple[int, int]] = Counter()
    big_casino: set[int] = set()
    # Расширенное покрытие мира: раньше distill «видел» только дуэли и казино —
    # подарки/кейсы/ачивки/клады/ранги/модерация в долгую память не попадали.
    jackpots: Counter[int] = Counter()        # сорвал джекпот в кейсе
    treasures: Counter[int] = Counter()       # кладоискатель
    achievements: Counter[int] = Counter()    # коллекционер ачивок
    rank_ups: Counter[int] = Counter()        # растущий боец (mmr rank up)
    gifts_received: Counter[int] = Counter()  # кому дарят подарки
    mod_hits: Counter[int] = Counter()        # кого регулярно нагибает модерация
    for e in events:
        if e.type == "duel_won" and e.actor_id:
            duel_wins[e.actor_id] += 1
            if e.target_id:
                duel_losses[e.target_id] += 1
                pair = tuple(sorted((e.actor_id, e.target_id)))
                pair_duels[pair] += 1
        elif e.type == "casino_big_win" and e.actor_id:
            big_casino.add(e.actor_id)
        elif e.type == "case_jackpot" and e.actor_id:
            jackpots[e.actor_id] += 1
        elif e.type == "treasure_found" and e.actor_id:
            treasures[e.actor_id] += 1
        elif e.type == "achievement_unlocked" and e.actor_id:
            achievements[e.actor_id] += 1
        elif e.type == "mmr_rank_up" and e.actor_id:
            rank_ups[e.actor_id] += 1
        elif e.type in ("gift_to_player", "gift_delivered"):
            # actor_id — получатель подарка (см. gifts/service.py).
            if e.actor_id:
                gifts_received[e.actor_id] += 1
        elif e.type in ("mod_ban", "mod_mute", "mod_warn", "mod_kick"):
            # target_id — кого нагнули; именно его и запоминаем как «штрафника».
            if e.target_id:
                mod_hits[e.target_id] += 1

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

    # Лохи по дуэлям: кого регулярно опускают (повод для рофла).
    for uid, losses in duel_losses.items():
        if losses >= 4 and duel_wins.get(uid, 0) < losses:
            _add(uid, "trait", f"{name_for(names, uid)} — постоянно сливает дуэли, груша для битья", 2)

    # Казино-везунчики.
    for uid in big_casino:
        _add(uid, "milestone", f"{name_for(names, uid)} срывал большой куш в казино", 2)

    # Джекпоты в кейсах — заметная удача, повод для зависти/подъёба.
    for uid, cnt in jackpots.items():
        if cnt >= 1:
            _add(uid, "milestone", f"{name_for(names, uid)} выбивал джекпот из кейса", 2)

    # Кладоискатели: кто регулярно находит клады.
    for uid, cnt in treasures.items():
        if cnt >= 2:
            _add(uid, "trait", f"{name_for(names, uid)} — удачливый кладоискатель", 1)

    # Коллекционеры ачивок.
    for uid, cnt in achievements.items():
        if cnt >= 3:
            _add(uid, "trait", f"{name_for(names, uid)} — задрот-достигатор, собирает ачивки", 1)

    # Растущие бойцы (серия повышений ранга).
    for uid, cnt in rank_ups.items():
        if cnt >= 2:
            _add(uid, "milestone", f"{name_for(names, uid)} быстро растёт в рейтинге дуэлей", 2)

    # Любимчики подарков — кому щедро дарят.
    for uid, cnt in gifts_received.items():
        if cnt >= 3:
            _add(uid, "trait", f"{name_for(names, uid)} — щедро осыпан подарками, чей-то любимчик", 1)

    # Штрафники: кого регулярно нагибает модерация (бан/мьют/варн/кик).
    for uid, cnt in mod_hits.items():
        if cnt >= 2:
            _add(uid, "trait", f"{name_for(names, uid)} — постоянный клиент модерации, ходит по краю", 2)

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
