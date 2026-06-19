"""Отложенная реакция друна — «заметил, смолчал, припомнит потом».

Живой участник чата не отвечает на КАЖДОЕ сообщение в ту же секунду. Часто он
замечает что-то (наезд, хвастовство, обещание, смешной косяк), молчит сейчас —
и припоминает это ПОЗЖЕ, когда подвернётся момент: «кстати, ты час назад
обещал занести, и где?». Это ломает паттерн «стимул→немедленный ответ», который
выдаёт бота с головой.

Механика дешёвая и без новых таблиц: «отложки» — список заметок в ``ai_settings``
(одна JSONB-строка). Реактивный путь, решив смолчать на сигнальной реплике,
иногда (по вероятности) НЕ роняет её в пустоту, а кладёт в очередь. Автономный
тик później достаёт «дозревшую» заметку (отлежалась нужное время, но не
протухла) и обыгрывает её — получается реакция с естественной задержкой.

Чистое ядро (отбор дозревших, ротация) тестируется без БД.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.models import AiSetting

logger = get_logger(__name__)

_KEY = "deferred_reactions"
# Сколько отложек держим в очереди (свежие вытесняют старые).
_MAX_PENDING = 12
# Заметка «дозревает» не раньше этого возраста (минуты) — иначе это просто
# отложенный на секунду ответ, а нам нужна именно ПАУЗА перед припоминанием.
_MIN_AGE_MIN = 12
# ...и протухает после этого (минуты): припоминать вчерашнюю мелочь — кринж.
_MAX_AGE_MIN = 180


@dataclass(frozen=True)
class Deferred:
    """Одна отложенная заметка «припомнить позже»."""

    user_id: int | None
    name: str
    gist: str          # суть: на что друн смолчал, но запомнил
    kind: str          # тип сигнала (roast/brag/...) — красит тон припоминания
    ts: str            # ISO-время, когда заметку отложили


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _load(session: AsyncSession) -> list[dict]:
    raw = await session.scalar(select(AiSetting.value).where(AiSetting.key == _KEY))
    return list(raw) if isinstance(raw, list) else []


async def _save(session: AsyncSession, items: list[dict]) -> None:
    stmt = (
        pg_insert(AiSetting)
        .values(key=_KEY, value=items)
        .on_conflict_do_update(index_elements=[AiSetting.key], set_={"value": items})
    )
    await session.execute(stmt)


async def stash(
    session: AsyncSession,
    *,
    user_id: int | None,
    name: str,
    gist: str,
    kind: str,
) -> bool:
    """Кладёт заметку в очередь отложенных реакций. Коммит — на вызывающем.

    Возвращает True, если заметка добавлена. Любой сбой глотаем (отложка —
    украшение, не должна ронять хэндлер).
    """
    g = (gist or "").strip()
    if not g:
        return False
    try:
        items = await _load(session)
        items.append({
            "user_id": user_id,
            "name": (name or "кто-то")[:64],
            "gist": g[:200],
            "kind": (kind or "")[:16],
            "ts": _now().isoformat(),
        })
        # Ротация: держим только последние N.
        if len(items) > _MAX_PENDING:
            items = items[-_MAX_PENDING:]
        await _save(session, items)
        return True
    except Exception:  # noqa: BLE001
        logger.debug("deferral stash failed", exc_info=True)
        return False


def _is_due(item: dict, now: datetime) -> bool:
    """Дозрела ли заметка: отлежалась ≥ _MIN_AGE, но ещё не протухла."""
    ts = item.get("ts")
    if not ts:
        return False
    try:
        age_min = (now - datetime.fromisoformat(ts)).total_seconds() / 60.0
    except (TypeError, ValueError):
        return False
    return _MIN_AGE_MIN <= age_min <= _MAX_AGE_MIN


def _partition_due(items: list[dict], now: datetime) -> tuple[dict | None, list[dict]]:
    """Возвращает (самая_старая_дозревшая, остаток_без_протухших).

    Чистая функция (тестируется без БД): протухшие выкидываем безусловно,
    дозревшую отдаём одну (самую старую), остальные оставляем в очереди.
    """
    kept: list[dict] = []
    due: dict | None = None
    for it in items:
        ts = it.get("ts")
        try:
            age_min = (now - datetime.fromisoformat(ts)).total_seconds() / 60.0
        except (TypeError, ValueError):
            continue  # битая запись — выкидываем
        if age_min > _MAX_AGE_MIN:
            continue  # протухла — выкидываем
        if due is None and age_min >= _MIN_AGE_MIN:
            due = it     # первая дозревшая (items в хронологии → самая старая)
            continue
        kept.append(it)
    return due, kept


async def take_due(session: AsyncSession) -> Deferred | None:
    """Достаёт одну дозревшую заметку и убирает её (и протухшие) из очереди.

    Коммит — на вызывающем. Возвращает ``Deferred`` или None, если припоминать
    нечего. Используется автономным тиком как ещё одна инициатива «без повода».
    """
    try:
        items = await _load(session)
        if not items:
            return None
        due, kept = _partition_due(items, _now())
        if due is None:
            # Нечего доставать, но могли отсеяться протухшие — перезапишем.
            if len(kept) != len(items):
                await _save(session, kept)
            return None
        await _save(session, kept)
        return Deferred(
            user_id=due.get("user_id"),
            name=str(due.get("name") or "кто-то"),
            gist=str(due.get("gist") or ""),
            kind=str(due.get("kind") or ""),
            ts=str(due.get("ts") or ""),
        )
    except Exception:  # noqa: BLE001
        logger.debug("deferral take_due failed", exc_info=True)
        return None
