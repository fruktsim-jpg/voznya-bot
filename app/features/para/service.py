"""Логика номинации «Пара дня».

Аналогична «Пидору дня», но выбираются двое. Реальные супруги (состоящие
в браке через бота) в выборе не участвуют.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import nomination_date
from app.models import DailyNomination
from app.repositories import marriages as marriages_repo
from app.repositories import nominations as nominations_repo
from app.repositories import users as users_repo
from app.services.economy import change_balance
from app.settings import balance

NOMINATION_TYPE = "para"


@dataclass
class ParaResult:
    """Результат вызова команды /пара."""

    status: str  # "not_enough" / "chosen" / "existing"
    first_id: int = 0
    second_id: int = 0
    opener_bonus: int = 0


async def get_or_choose_para(session: AsyncSession, opener_id: int) -> ParaResult:
    """Возвращает текущую Пару Дня или выбирает новую (если день новый)."""
    game_date = nomination_date()

    existing = await nominations_repo.get_nomination(
        session, NOMINATION_TYPE, game_date
    )
    if existing is not None:
        return ParaResult(
            status="existing",
            first_id=existing.user_id or 0,
            second_id=existing.user_id_2 or 0,
        )

    married = await marriages_repo.get_married_user_ids(session)
    active_ids = await users_repo.get_active_user_ids(
        session, balance.NOMINATION_ACTIVE_DAYS, exclude=married
    )
    if len(active_ids) < balance.NOMINATION_MIN_CANDIDATES:
        return ParaResult(status="not_enough")

    # Гарантированно два разных человека (один не может попасть дважды).
    first_id, second_id = random.sample(active_ids, 2)

    stmt = (
        pg_insert(DailyNomination)
        .values(
            nomination_type=NOMINATION_TYPE,
            nomination_date=game_date,
            user_id=first_id,
            user_id_2=second_id,
            opened_by=opener_id,
        )
        .on_conflict_do_nothing(constraint="uq_nomination_type_date")
        .returning(DailyNomination.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()

    if inserted_id is None:
        existing = await nominations_repo.get_nomination(
            session, NOMINATION_TYPE, game_date
        )
        return ParaResult(
            status="existing",
            first_id=(existing.user_id or 0) if existing else 0,
            second_id=(existing.user_id_2 or 0) if existing else 0,
        )

    bonus = balance.NOMINATION_OPEN_BONUS
    await change_balance(session, opener_id, bonus, "nomination", {"type": "para"})

    return ParaResult(
        status="chosen", first_id=first_id, second_id=second_id, opener_bonus=bonus
    )
