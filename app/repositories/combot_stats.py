"""Историческая надстройка Combot над текущей статистикой бота.

Единственное место, где считается «общий» счётчик сообщений игрока:

    total_messages = historical_messages_from_combot + current_messages_from_voznya

Combot ``user_id`` совпадает с Telegram ``user_id`` (он же ключ ``users``), поэтому
объединение прямое по ``user_id`` — без account_links и без таблиц-маппингов.

Если для игрока нет строки в ``combot_user_stats`` (или таблица ещё не создана —
миграция 0012 не накатана), исторический вклад = 0 и система ведёт себя как
раньше: ``total_messages == current_messages``.

Модуль только ЧИТАЕТ. Не трогает users/balance/transactions/inventory/shop/gift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CombotUserStats


@dataclass(frozen=True)
class CombotOverlay:
    """Исторический вклад Combot для одного игрока."""

    # Сообщения по данным Combot (0, если записи нет).
    historical_messages: int = 0
    # Дата входа в чат по Combot (None, если неизвестна).
    joined_at: datetime | None = None


def total_messages(current_messages: int, overlay: CombotOverlay | None) -> int:
    """Единое вычисление общего счётчика сообщений.

    ``current_messages`` — текущий ``users.messages_count`` (счёт Возни).
    ``overlay`` — исторический вклад Combot (или None → 0).
    """
    historical = overlay.historical_messages if overlay else 0
    return int(current_messages or 0) + int(historical or 0)


async def get_combot_overlay(session: AsyncSession, user_id: int) -> CombotOverlay:
    """Возвращает исторический вклад Combot для игрока.

    Безопасна к отсутствию таблицы ``combot_user_stats`` (импорт ещё не
    выполнялся / миграция не накатана) — в этом случае вернёт пустой overlay.
    """
    # Если таблицы нет — тихо возвращаем пустой overlay (работаем как раньше).
    exists = await session.scalar(text("SELECT to_regclass('combot_user_stats')"))
    if not exists:
        return CombotOverlay()

    try:
        row = (
            await session.execute(
                select(
                    CombotUserStats.messages, CombotUserStats.joined_at
                ).where(CombotUserStats.user_id == user_id)
            )
        ).first()
    except ProgrammingError:
        # Подстраховка на гонку «таблица исчезла между проверкой и запросом».
        return CombotOverlay()

    if row is None:
        return CombotOverlay()
    return CombotOverlay(historical_messages=int(row[0] or 0), joined_at=row[1])
