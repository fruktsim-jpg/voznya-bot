"""Автономное поведение Тёмного друна (#8).

Друн не только отвечает на обращения — он ЖИВЁТ в чате сам: комментирует
значимые события мира, когда они случаются, по собственному почину. Это и есть
«живое существо», а не реактивный бот.

Механика (дёшево и безопасно):
* фоновая джоба раз в N минут смотрит свежие ``world_events`` с severity ≥ порога;
* если есть новое значимое событие, которого друн ещё не комментировал
  (трекаем high-water-mark id в ``ai_settings``), он генерит короткую реакцию
  в образе и постит в целевой чат;
* жёсткие предохранители: дневной кап постов (``posts_per_day_max``), пропуск
  если чат прямо сейчас кипит (не перебиваем живую беседу), один пост за тик.

Любой сбой джобы — молча в лог, бот продолжает жить.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.core.money import money
from app.core.utils import now_utc
from app.features.drun import config as drun_config
from app.features.drun import memory as drun_memory
from app.features.drun import service as drun_service
from app.features.drun.names import name_for, resolve_names
from app.models import AiSetting, WorldEvent
from app.services import world_events as _we

logger = get_logger(__name__)

# Ключ high-water-mark: id последнего прокомментированного события.
_KEY_LAST_EVENT = "autonomous_last_event_id"
# на первом запуске (high-water-mark пуст) друн мог бы выдать событие недельной
# давности за «только что произошло».
_EVENT_FRESH_MIN = 30
# Собственные действия друна — он на них не «реагирует» как на чужую новость.
_SELF_EVENT_TYPES = (_we.EVENT_DRUN_TAX, _we.EVENT_DRUN_GRANT)
# Анти-нытьё: один и тот же подмеченный паттерн друн не комментирует чаще, чем
# раз в N часов (ключ ai_settings = последний слепок «паттерн→время»).
_KEY_LAST_PATTERN = "autonomous_last_pattern"
_PATTERN_COOLDOWN_H = 6


async def _get_last_event_id(session: AsyncSession) -> int:
    """Читает high-water-mark id последнего прокомментированного события."""
    raw = await session.scalar(
        select(AiSetting.value).where(AiSetting.key == _KEY_LAST_EVENT)
    )
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


async def _set_last_event_id(session: AsyncSession, event_id: int) -> None:
    """Сохраняет high-water-mark (upsert в ai_settings)."""
    stmt = (
        pg_insert(AiSetting)
        .values(key=_KEY_LAST_EVENT, value=event_id)
        .on_conflict_do_update(
            index_elements=[AiSetting.key], set_={"value": event_id}
        )
    )
    await session.execute(stmt)


def _describe_event(ev: WorldEvent, names: dict[int, str]) -> str:
    """Человекочитаемое описание события для задания нарратору."""
    who = name_for(names, ev.actor_id) if ev.actor_id else ""
    tgt = name_for(names, ev.target_id) if ev.target_id else ""
    amount = f" на {money(ev.amount)}" if ev.amount else ""
    parts = [f"тип={ev.type}"]
    if who:
        parts.append(f"кто={who}")
    if tgt:
        parts.append(f"кого={tgt}")
    if amount:
        parts.append(f"сумма={amount.strip()}")
    return ", ".join(parts)


async def comment_on_fresh_events(
    session: AsyncSession, *, channel: str = "chat"
) -> str | None:
    """Находит свежее значимое событие и генерит реакцию друна.

    Возвращает текст реакции (для отправки в чат) или None, если комментировать
    нечего / сработал предохранитель. Коммит делает вызывающий.
    """
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return None
    # Автономный постинг — отдельный явный опт-ин (по умолчанию off): друн не
    # должен сам заговаривать в чате только потому, что включён реактивный режим.
    if not cfg.autonomous_enabled:
        return None

    # Дневной кап постов — друн не спамер.
    posts_today = await drun_memory.count_replies_today(session, channel=channel)
    if posts_today >= cfg.posts_per_day_max:
        logger.debug("autonomous: daily cap reached (%d)", posts_today)
        return None

    # «Чувство комнаты»: governor решает, можно ли вообще сейчас вкидываться и
    # не пора ли наоборот РАСШЕВЕЛИТЬ мёртвый чат. Перебивать живую/кипящую
    # беседу или лезть в абуз-режиме — нельзя.
    from app.features.drun import governor as drun_governor

    verdict = await drun_governor.assess(session, channel=channel)
    if not verdict.may_autopost:
        logger.debug("autonomous: governor blocks (%s)", verdict.pulse.value)
        return None
    if verdict.should_stir:
        # Чат мёртвый и свежих событий может не быть — сами создаём движ.
        stir = await _stir_dead_chat(session, channel=channel)
        if stir:
            return stir

    last_id = await _get_last_event_id(session)
    # Берём САМОЕ СТАРОЕ ещё не прокомментированное, но всё ещё свежее событие
    # (id ASC). Раньше брали новейшее (created_at DESC) и сразу двигали
    # watermark на него — при нескольких событиях за один тик все, кроме
    # последнего, терялись навсегда. Теперь очередь дренится по одному за тик.
    # Исключаем СОБСТВЕННЫЕ econ-события друна (drun_tax/drun_grant): он не
    # должен «реагировать» на свои же действия как на новость со стороны.
    fresh_since = now_utc() - timedelta(minutes=_EVENT_FRESH_MIN)
    ev = (
        await session.execute(
            select(WorldEvent)
            .where(WorldEvent.id > last_id)
            .where(WorldEvent.severity >= cfg.min_severity)
            .where(WorldEvent.created_at >= fresh_since)
            .where(WorldEvent.type.notin_(_SELF_EVENT_TYPES))
            .order_by(WorldEvent.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ev is None:
        # Нет свежих событий — но друн всё равно НАБЛЮДАТЕЛЬ. Пытаемся заметить
        # поведенческий паттерн (тильт/серия) и прокомментировать по своему
        # почину. Это инициатива без триггера-события — суть агентности.
        return await _notice_pattern(session, channel=channel)

    # Двигаем high-water-mark СРАЗУ (даже если генерация упадёт) — чтобы не
    # зациклиться на одном событии при повторных сбоях LLM.
    await _set_last_event_id(session, ev.id)

    names = await resolve_names(
        session, [i for i in (ev.actor_id, ev.target_id) if i]
    )
    desc = _describe_event(ev, names)
    task = (
        "В мире Возни только что произошло событие, и ты, как живой участник "
        "чата, по своему почину кидаешь короткую реакцию-комментарий (1-2 "
        "фразы, в образе, с подколом или эмоцией по настроению). Не "
        "представляйся, не объясняй что ты бот, не зачитывай статистику. Просто "
        "живая реплика по поводу события.\n\n"
        f"# СОБЫТИЕ: {desc}"
    )
    result = await drun_service.generate(
        session,
        task=task,
        subject_id=ev.actor_id,
        channel=channel,
        include_events=True,
        trigger_event_id=ev.id,
        memory_kind="monologue",
        role=drun_config.ROLE_NARRATOR,
    )
    if not result.ok or not result.text:
        return None
    return result.text


async def _stir_dead_chat(
    session: AsyncSession, *, channel: str
) -> str | None:
    """Генерит «движ» в мёртвый чат: провокация/тема, чтобы зашли люди.

    Это НЕ реакция на событие, а инициатива друна — расшевелить тишину. Без
    выдачи ешек (это решает владелец), чисто социальный вброс в образе.
    """
    task = (
        "В чате Возни мёртвая тишина, никто не пишет. Ты — живой дух этого "
        "чата и тебе скучно. Кинь ОДНУ короткую провокационную реплику, чтобы "
        "расшевелить народ и вытащить людей: дерзкий вопрос, подъёб «спящих», "
        "вызов, тема для движа, странное наблюдение. БЕЗ цифр и статистики, без "
        "представления себя ботом. Живой вброс в твоём стиле — так, чтобы "
        "захотелось ответить."
    )
    result = await drun_service.generate(
        session,
        task=task,
        channel=channel,
        memory_kind="monologue",
        role=drun_config.ROLE_NARRATOR,
    )
    if not result.ok or not result.text:
        return None
    return result.text


async def _get_pattern_mark(session: AsyncSession) -> dict:
    """Читает слепок «паттерн→ISO-время последнего коммента» из ai_settings."""
    raw = await session.scalar(
        select(AiSetting.value).where(AiSetting.key == _KEY_LAST_PATTERN)
    )
    return raw if isinstance(raw, dict) else {}


async def _set_pattern_mark(session: AsyncSession, mark: dict) -> None:
    stmt = (
        pg_insert(AiSetting)
        .values(key=_KEY_LAST_PATTERN, value=mark)
        .on_conflict_do_update(
            index_elements=[AiSetting.key], set_={"value": mark}
        )
    )
    await session.execute(stmt)


async def _notice_pattern(
    session: AsyncSession, *, channel: str
) -> str | None:
    """Инициатива без события: друн замечает поведенческий паттерн и влезает.

    Живой смотритель чата не ждёт «события из шины» — он сам видит, что кто-то
    проигрался в хлам, а кто-то фермит десятый день. Берём САМЫЙ выраженный
    свежий паттерн и комментируем (анти-нытьё: один паттерн не чаще, чем раз в
    ``_PATTERN_COOLDOWN_H`` часов). Дёшево: отбор по индексируемым полям, LLM —
    только когда уже решили говорить.
    """
    from app.models import User

    mark = await _get_pattern_mark(session)
    now = now_utc()

    def _recent(key: str) -> bool:
        ts = mark.get(key)
        if not ts:
            return False
        try:
            return (now - datetime.fromisoformat(ts)).total_seconds() < (
                _PATTERN_COOLDOWN_H * 3600
            )
        except (TypeError, ValueError):
            return False

    async def _emit(subject, task: str, key: str) -> str | None:
        result = await drun_service.generate(
            session, task=task, subject_id=subject.id, channel=channel,
            include_events=False, memory_kind="monologue",
            role=drun_config.ROLE_NARRATOR,
        )
        if result.ok and result.text:
            mark[key] = now.isoformat()
            await _set_pattern_mark(session, mark)
            return result.text
        return None

    async def _emit_world(task: str, key: str) -> str | None:
        """Эмит без конкретного субъекта — для сюжетов/летописи мира."""
        result = await drun_service.generate(
            session, task=task, subject_id=None, channel=channel,
            include_events=False, memory_kind="monologue",
            role=drun_config.ROLE_NARRATOR,
        )
        if result.ok and result.text:
            mark[key] = now.isoformat()
            await _set_pattern_mark(session, mark)
            return result.text
        return None

    # Кандидат 0: летопись — друн сам поднимает тянущийся сюжет или вспоминает
    # легенду/прогноз. Это ядро агентности: он не ждёт события, а ВЕДЁТ
    # повествование мира. Редко (свой кулдаун), чтобы не превратить в нытьё.
    if not _recent("worldview_story"):
        try:
            from app.features.drun import worldview as drun_worldview

            wv = await drun_worldview.worldview_block(session)
            if wv and "Сюжеты в развитии" in wv:
                task = (
                    "Ты — живой дух чата Возни и его ЛЕТОПИСЕЦ. У тебя есть свои "
                    "сюжеты, прогнозы и легенды (ниже в контексте — блок ЛЕТОПИСЬ). "
                    "Сам, без повода, ВКИНЬ в чат ОДНУ живую реплику: продвинь "
                    "тянущийся сюжет, припомни легенду или напомни про свой "
                    "прогноз — дерзко, в образе, коротко. Не зачитывай списком, "
                    "выбери ОДНУ нить и обыграй её как ведущий шоу."
                )
                out = await _emit_world(task, "worldview_story")
                if out:
                    return out
        except Exception:  # noqa: BLE001
            logger.debug("worldview initiative failed", exc_info=True)

    # Кандидат 1: глубокий тильт в казино (серия проигрышей) — сочнейший роаст.
    if not _recent("casino_tilt"):
        u = (
            await session.execute(
                select(User)
                .where(User.casino_loss_streak >= 5)
                .order_by(User.casino_loss_streak.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if u is not None:
            names = await resolve_names(session, [u.id])
            who = name_for(names, u.id)
            task = (
                "Ты — живой дух чата Возни и любишь подъёбывать. Ты ЗАМЕТИЛ сам "
                "(никто тебя не звал), что игрок сливается в казино серией. Кинь "
                "ОДНУ дерзкую реплику-подъёб, по имени, коротко и метко. Без "
                "статистики и перечисления цифр.\n\n"
                f"# КОГО ПОДКОЛОТЬ: {who} — {u.casino_loss_streak} проигрышей "
                "подряд в казино"
            )
            out = await _emit(u, task, "casino_tilt")
            if out:
                return out

    # Кандидат 2: упорный фермер (большая серия) — повод поддеть «трудягу».
    if not _recent("farm_grinder"):
        u = (
            await session.execute(
                select(User)
                .where(User.farm_streak >= 10)
                .order_by(User.farm_streak.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if u is not None:
            names = await resolve_names(session, [u.id])
            who = name_for(names, u.id)
            task = (
                "Ты — живой дух чата Возни. Ты сам подметил, что игрок задрот "
                "фермит без пропусков длинной серией. Кинь ОДНУ живую реплику: "
                "подколи трудягу или признай упорство, по имени, коротко, в "
                "образе.\n\n"
                f"# О КОМ: {who} — фермит {u.farm_streak} дней подряд"
            )
            out = await _emit(u, task, "farm_grinder")
            if out:
                return out

    return None


def setup_autonomous_poster(
    scheduler,
    bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    chat_id: int,
    *,
    minutes: int = 7,
) -> None:
    """Регистрирует фоновую джобу автономного комментирования событий.

    Раз в ``minutes`` минут друн смотрит, не случилось ли значимого события, и
    если да — по своему почину кидает реплику в целевой чат. Все предохранители
    (дневной кап, занятость чата, идемпотентность) — внутри
    :func:`comment_on_fresh_events`.
    """

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                text = await comment_on_fresh_events(session)
                await session.commit()
            if text:
                await bot.send_message(chat_id, text)
                logger.info("drun autonomous: posted event comment")
        except Exception:  # noqa: BLE001
            logger.warning("drun autonomous poster failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_autonomous_poster",
        replace_existing=True,
    )