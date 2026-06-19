"""Бизнес-логика репутации: разбор фразы и применение изменения.

Здесь живут две вещи:

* :func:`classify` — превращает текст ответа в ``+1`` / ``-1`` / ``None``
  (распознаёт фразу-алиас);
* :func:`apply_reputation` — применяет изменение со всеми ограничениями
  (само-оценка, боты, удалённые, антиспам 12 ч) и пишет строку в журнал.

Репутация изолирована от ешек/XP/сообщений/магазина/инвентаря/Combot.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories import reputation as rep_repo
from app.settings import reputation as rep_settings

# Множества алиасов в нижнем регистре для быстрой точной сверки.
_POSITIVE = {a.lower() for a in rep_settings.POSITIVE_ALIASES}
_NEGATIVE = {a.lower() for a in rep_settings.NEGATIVE_ALIASES}


def _normalize(text: str) -> str:
    """Приводит текст к канону для сверки: нижний регистр, схлопнутые пробелы."""
    return " ".join(text.lower().split())


def classify(text: str | None) -> int | None:
    """Возвращает +1 / -1 для фразы-алиаса либо None, если фраза не подходит.

    Сверка точная: всё сообщение целиком должно быть одной из фраз
    (с точностью до регистра и лишних пробелов). Это исключает ложные
    срабатывания на длинных сообщениях, где алиас — лишь часть текста.
    """
    if not text:
        return None
    norm = _normalize(text)
    if not norm:
        return None
    if norm in _POSITIVE:
        return 1
    if norm in _NEGATIVE:
        return -1
    return None


@dataclass(frozen=True)
class RepResult:
    """Итог попытки изменить репутацию."""

    # Статусы: applied / self / bot / deleted / cooldown.
    status: str
    value: int = 0
    new_score: int = 0
    retry_after_seconds: float = 0.0


async def apply_reputation(
    session: AsyncSession,
    *,
    giver_user_id: int,
    target_user_id: int,
    target_is_bot: bool,
    value: int,
    reason: str | None,
) -> RepResult:
    """Применяет изменение репутации с проверкой всех ограничений.

    Порядок проверок: само-оценка → бот → удалённый/неизвестный → антиспам.
    При успехе пишет строку в журнал (commit — на вызывающем коде).
    """
    # Нельзя оценивать самого себя.
    if giver_user_id == target_user_id:
        return RepResult(status="self")

    # Нельзя оценивать ботов.
    if target_is_bot:
        return RepResult(status="bot")

    # Нельзя оценивать удалённых/неизвестных боту пользователей.
    target = await session.get(User, target_user_id)
    if target is None:
        return RepResult(status="deleted")

    # Антиспам: одному человеку — не чаще раза в N часов.
    retry_after = await rep_repo.seconds_until_available(
        session,
        giver_user_id=giver_user_id,
        target_user_id=target_user_id,
        cooldown_hours=rep_settings.REPUTATION_COOLDOWN_HOURS,
    )
    if retry_after > 0:
        return RepResult(status="cooldown", retry_after_seconds=retry_after)

    # Пишем изменение и возвращаем актуальный итог.
    entry_id = await rep_repo.add_entry(
        session,
        giver_user_id=giver_user_id,
        target_user_id=target_user_id,
        value=value,
        reason=reason,
    )
    await session.flush()

    summary = await rep_repo.get_summary(session, target_user_id)

    # Проекция в world_events: друн видит соц-динамику уважения (кто кому
    # накинул/снял), а не только итоговый счёт. Идемпотентно по записи журнала.
    from app.services import world_events

    await world_events.emit_safe(
        session,
        type=world_events.EVENT_REPUTATION,
        actor_id=giver_user_id,
        target_id=target_user_id,
        amount=value,
        ref_table="reputation_entries",
        ref_id=entry_id,
        meta={"new_score": summary.score, "reason": (reason or "")[:200]},
    )

    return RepResult(status="applied", value=value, new_score=summary.score)
