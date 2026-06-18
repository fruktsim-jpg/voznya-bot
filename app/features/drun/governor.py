"""Activity Governor — «чувство комнаты» друна (мозг автономности).

Друн не должен слепо постить по таймеру. Он должен ЧИТАТЬ состояние чата и
вести себя как живой смотритель:

* МЁРТВО (тишина) → СОЗДАТЬ ДВИЖ: эвент/клад/провокационный вопрос, чтобы
  вытащить людей в чат;
* НОРМ (люди сами неспешно общаются) → не мешать, лишь изредка тонко вкинуть;
* КИПИТ (живая активная беседа без него) → МОЛЧАТЬ, не перебивать;
* АБУЗ (каждый второй долбит бота / спам в его адрес) → ПРИТОРМОЗИТЬ: короче,
  суше, реже, чтобы не превращаться в игрушку-долбилку.

Классификация — чистая функция от метрик (легко тестировать). Метрики берём из
``memory.pulse_stats`` (всего реплик + уникальных авторов за окно) и из доли
обращений к боту. Сам governor НЕ шлёт сообщений — он возвращает решение,
которым пользуются автопостер и сервис ответов.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.features.drun import memory as drun_memory

logger = get_logger(__name__)


class Pulse(str, Enum):
    """Режим активности чата."""

    DEAD = "dead"        # тишина — нужно расшевелить
    NORMAL = "normal"    # спокойное живое общение
    HOT = "hot"          # кипит, людям и без бота хорошо
    ABUSE = "abuse"      # бота задёргали, пора притормозить


@dataclass(frozen=True)
class Verdict:
    """Решение governor'а: режим + что можно делать."""

    pulse: Pulse
    may_autopost: bool      # можно ли по своему почину вкинуть/создать движ
    should_stir: bool       # стоит ли активно создавать движ (мёртвый чат)
    throttle: bool          # резать частоту/длину ответов (абуз)
    note: str               # короткая подсказка для промпта/лога


# Пороги (окно по умолчанию 15 минут). Подобраны под живой, но не огромный чат;
# вынесены в константы, чтобы было видно и легко крутить.
_HOT_MSGS = 25            # ≥ столько реплик за окно — чат кипит
_HOT_SPEAKERS = 4         # ...и говорит реально несколько человек
_DEAD_MSGS = 2            # ≤ столько — практически тишина
_ABUSE_BOT_RATIO = 0.55   # доля обращений к боту, выше которой это «долбёжка»
_ABUSE_MIN_MSGS = 8       # ...но только если вообще есть заметный объём


def classify(
    total_msgs: int, speakers: int, bot_directed: int
) -> Verdict:
    """Классифицирует пульс чата по метрикам окна. Чистая функция.

    :param total_msgs: всего живых реплик игроков за окно.
    :param speakers: уникальных авторов за окно.
    :param bot_directed: сколько из реплик были обращены к боту (упоминание/реплай).
    """
    bot_ratio = (bot_directed / total_msgs) if total_msgs > 0 else 0.0

    # АБУЗ: заметный объём и при этом большинство — в адрес бота, причём говорящих
    # мало (один-двое висят на боте). Если людей много и все болтают — это HOT,
    # а не абуз, даже если бота поминают.
    if (
        total_msgs >= _ABUSE_MIN_MSGS
        and bot_ratio >= _ABUSE_BOT_RATIO
        and speakers <= 3
    ):
        return Verdict(
            Pulse.ABUSE, may_autopost=False, should_stir=False, throttle=True,
            note="чат задёрбал тебя обращениями — отвечай короче и суше, "
                 "не давай себя абузить, переведи стрелки на общение людей "
                 "между собой",
        )

    # КИПИТ: много реплик и реально несколько участников — не лезь.
    if total_msgs >= _HOT_MSGS and speakers >= _HOT_SPEAKERS:
        return Verdict(
            Pulse.HOT, may_autopost=False, should_stir=False, throttle=False,
            note="чат и без тебя кипит — не перебивай живую беседу",
        )

    # МЁРТВО: тишина — надо расшевелить.
    if total_msgs <= _DEAD_MSGS:
        return Verdict(
            Pulse.DEAD, may_autopost=True, should_stir=True, throttle=False,
            note="чат мёртвый — твоя задача расшевелить людей: задай движ, "
                 "вкинь провокацию/тему/движуху, чтобы народ появился",
        )

    # НОРМ: спокойное общение — можно изредка тонко вкинуть, но без напора.
    return Verdict(
        Pulse.NORMAL, may_autopost=True, should_stir=False, throttle=False,
        note="чат живой, но спокойный — можешь изредка тонко вкинуться в тему",
    )


async def assess(
    session: AsyncSession, *, channel: str = "chat", minutes: int = 15
) -> Verdict:
    """Снимает метрики окна и возвращает вердикт. Сбой — безопасный NORMAL.

    Результат кэшируется на ``_CACHE_TTL`` секунд на канал: ``respond`` зовёт
    assess на КАЖДУЮ реплику, а пульс чата между соседними сообщениями почти не
    меняется — нет смысла гонять COUNT-запросы на каждое сообщение. Автопостер
    (раз в 7 мин) кэш просто не успевает переиспользовать — и не должен.
    """
    cached = _cache_get(channel)
    if cached is not None:
        return cached
    try:
        total, speakers = await drun_memory.pulse_stats(
            session, channel=channel, minutes=minutes
        )
        # Прокси «обращений к боту»: число его ответов за окно (единый helper в
        # memory — семантика role='assistant' живёт там, без дубля запроса тут).
        bot_directed = await drun_memory.bot_replies_in_window(
            session, channel=channel, minutes=minutes
        )
        verdict = classify(total, speakers, bot_directed)
        logger.debug(
            "governor pulse=%s msgs=%d speakers=%d bot=%d",
            verdict.pulse.value, total, speakers, bot_directed,
        )
        _cache_put(channel, verdict)
        return verdict
    except Exception:  # noqa: BLE001
        logger.debug("governor assess failed; defaulting NORMAL", exc_info=True)
        return Verdict(
            Pulse.NORMAL, may_autopost=True, should_stir=False, throttle=False,
            note="",
        )


# Короткий процессный кэш вердикта на канал, чтобы не бить COUNT-ами на каждую
# реплику в хот-пути. TTL мал: всплеск сообщений переиспользует один снимок.
_CACHE_TTL = 30.0
_verdict_cache: dict[str, tuple[float, Verdict]] = {}


def _cache_get(channel: str) -> Verdict | None:
    hit = _verdict_cache.get(channel)
    if hit is None:
        return None
    ts, verdict = hit
    if time.monotonic() - ts >= _CACHE_TTL:
        return None
    return verdict


def _cache_put(channel: str, verdict: Verdict) -> None:
    _verdict_cache[channel] = (time.monotonic(), verdict)
