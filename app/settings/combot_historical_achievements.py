"""Слой достижений на основе исторических данных Combot (только правила).

Это ЧИСТЫЙ слой определений: пороги и функция-классификатор. Здесь нет работы с
БД, нет начисления ешек и нет записи в ``user_achievements`` — только «по данным
снимка Combot вычислить, какие исторические бейджи заслужены». Реальная выдача
(маппинг на боевые достижения, награды, нотификации) — отдельное решение и
отдельная задача; этот модуль её НЕ выполняет.

Источник входных данных — таблица ``combot_user_stats`` (см. модель
``CombotUserStats``): поля ``joined_at`` / ``days_since_joined`` / ``messages``.

Пороги — стартовые, подобраны под текущий срез (405 участников, top-1 ≈ 14k
сообщений, ~2 года истории). Их можно править здесь, в одном месте, не трогая
импорт и схему.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class HistoricalTier:
    """Один исторический бейдж и условие его получения."""

    code: str
    emoji: str
    name: str
    description: str
    # Порог по дням в чате (joined). None — не учитывается.
    min_days_in_chat: int | None = None
    # Порог по числу сообщений. None — не учитывается.
    min_messages: int | None = None


# --- Определения исторических бейджей (по возрастанию «престижа») ------------
# Пороги намеренно консервативные; калибруются по факту импорта.
HISTORICAL_TIERS: list[HistoricalTier] = [
    HistoricalTier(
        code="combot_activist",
        emoji="🔥",
        name="Активист",
        description="Заметная активность в чате по истории Combot.",
        min_messages=500,
    ),
    HistoricalTier(
        code="combot_veteran",
        emoji="🎖️",
        name="Ветеран",
        description="Давно в чате и стабильно пишет.",
        min_days_in_chat=180,
        min_messages=1000,
    ),
    HistoricalTier(
        code="combot_oldtimer",
        emoji="⏳",
        name="Старожил",
        description="Один из самых давних участников чата.",
        min_days_in_chat=365,
    ),
    HistoricalTier(
        code="combot_legend",
        emoji="👑",
        name="Легенда",
        description="Топ по сообщениям за всю историю Возни.",
        min_messages=5000,
    ),
]

HISTORICAL_TIERS_BY_CODE: dict[str, HistoricalTier] = {
    t.code: t for t in HISTORICAL_TIERS
}


def _days_in_chat(
    joined_at: datetime | None,
    days_since_joined: int | None,
    *,
    now: datetime | None = None,
) -> int | None:
    """Вычисляет число дней в чате.

    Берёт готовое ``days_since_joined`` (поле ``dsj`` Combot), иначе считает по
    ``joined_at``. Возвращает None, если ничего не известно.
    """
    if days_since_joined is not None:
        return days_since_joined
    if joined_at is None:
        return None
    ref = now or datetime.now(timezone.utc)
    if joined_at.tzinfo is None:
        joined_at = joined_at.replace(tzinfo=timezone.utc)
    return max(0, (ref - joined_at).days)


def qualifies(tier: HistoricalTier, *, days_in_chat: int | None, messages: int) -> bool:
    """Проверяет, заслужен ли бейдж по снимку (чистая функция, без БД)."""
    if tier.min_messages is not None and messages < tier.min_messages:
        return False
    if tier.min_days_in_chat is not None:
        if days_in_chat is None or days_in_chat < tier.min_days_in_chat:
            return False
    return True


def evaluate_historical_tiers(
    *,
    messages: int,
    joined_at: datetime | None = None,
    days_since_joined: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Возвращает коды заслуженных исторических бейджей для одного игрока.

    Чистая функция: принимает поля снимка Combot, отдаёт список ``code``.
    Ничего не пишет и не начисляет — только вычисление по порогам.
    """
    days = _days_in_chat(joined_at, days_since_joined, now=now)
    return [
        tier.code
        for tier in HISTORICAL_TIERS
        if qualifies(tier, days_in_chat=days, messages=messages)
    ]
