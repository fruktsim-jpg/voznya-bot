"""Логика номинации «Пидор дня».

Выбор ленивый: пока никто не вызвал /пидор после 12:00, статус не меняется.
Первый вызов после сброса выбирает нового Пидора Дня среди активных за
последние N дней. Результат фиксируется до следующего игрового дня.

Гонка (несколько одновременных «первых» вызовов) решается уникальным
ограничением (nomination_type, nomination_date) и INSERT ... ON CONFLICT.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import nomination_date
from app.models import DailyNomination, User
from app.repositories import nominations as nominations_repo
from app.repositories import users as users_repo
from app.services.economy import change_balance
from app.settings import balance

NOMINATION_TYPE = "pidor"


@dataclass
class PidorResult:
    """Результат вызова команды /пидор."""

    status: str  # "not_enough" / "chosen" / "existing"
    winner_id: int = 0
    count: int = 0
    opener_bonus: int = 0


async def get_or_choose_pidor(
    session: AsyncSession, opener_id: int
) -> PidorResult:
    """Возвращает текущего Пидора Дня или выбирает нового (если день новый)."""
    game_date = nomination_date()

    existing = await nominations_repo.get_nomination(
        session, NOMINATION_TYPE, game_date
    )
    if existing is not None:
        winner = await session.get(User, existing.user_id)
        count = winner.pidor_count if winner else 0
        return PidorResult(status="existing", winner_id=existing.user_id or 0, count=count)

    # День ещё не открыт — пытаемся стать тем, кто открывает.
    active_ids = await users_repo.get_active_user_ids(
        session, balance.NOMINATION_ACTIVE_DAYS
    )
    if len(active_ids) < balance.NOMINATION_MIN_CANDIDATES:
        return PidorResult(status="not_enough")

    winner_id = random.choice(active_ids)

    stmt = (
        pg_insert(DailyNomination)
        .values(
            nomination_type=NOMINATION_TYPE,
            nomination_date=game_date,
            user_id=winner_id,
            opened_by=opener_id,
        )
        .on_conflict_do_nothing(constraint="uq_nomination_type_date")
        .returning(DailyNomination.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()

    if inserted_id is None:
        # Кто-то успел открыть день первым — отдаём уже выбранного.
        existing = await nominations_repo.get_nomination(
            session, NOMINATION_TYPE, game_date
        )
        winner = await session.get(User, existing.user_id) if existing else None
        return PidorResult(
            status="existing",
            winner_id=(existing.user_id or 0) if existing else 0,
            count=winner.pidor_count if winner else 0,
        )

    # Мы открыли день: засчитываем победителю и выдаём бонус открывшему.
    winner = await session.get(User, winner_id, with_for_update=True)
    assert winner is not None
    winner.pidor_count += 1

    bonus = balance.NOMINATION_OPEN_BONUS
    await change_balance(session, opener_id, bonus, "nomination", {"type": "pidor"})

    return PidorResult(
        status="chosen", winner_id=winner_id, count=winner.pidor_count, opener_bonus=bonus
    )
