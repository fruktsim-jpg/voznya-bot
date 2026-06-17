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

from datetime import timedelta

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

logger = get_logger(__name__)

# Ключ high-water-mark: id последнего прокомментированного события.
_KEY_LAST_EVENT = "autonomous_last_event_id"
# Если чат сейчас активнее этого — не перебиваем живую беседу автопостом.
_BUSY_CHAT_THRESHOLD = 12
# Комментируем только ДЕЙСТВИТЕЛЬНО свежие события: без нижней границы по времени
# на первом запуске (high-water-mark пуст) друн мог бы выдать событие недельной
# давности за «только что произошло».
_EVENT_FRESH_MIN = 30


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

    # Не перебиваем живую беседу — если чат кипит, люди и так общаются.
    hot = await drun_memory.recent_chat_count(session, channel=channel, seconds=300)
    if hot >= _BUSY_CHAT_THRESHOLD:
        logger.debug("autonomous: chat busy (%d), skip", hot)
        return None

    last_id = await _get_last_event_id(session)
    # Самое свежее значимое событие новее high-water-mark И не старше окна — чтобы
    # на первом запуске (last_id=0) не выдать древнее событие за «только что».
    fresh_since = now_utc() - timedelta(minutes=_EVENT_FRESH_MIN)
    ev = (
        await session.execute(
            select(WorldEvent)
            .where(WorldEvent.id > last_id)
            .where(WorldEvent.severity >= cfg.min_severity)
            .where(WorldEvent.created_at >= fresh_since)
            .order_by(WorldEvent.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ev is None:
        return None

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