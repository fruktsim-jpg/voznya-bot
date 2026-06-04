"""Запросы, связанные с суточными номинациями (Пидор/Пара дня)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyNomination


async def get_nomination(
    session: AsyncSession, nomination_type: str, nomination_date: date
) -> DailyNomination | None:
    """Возвращает номинацию указанного типа за конкретный игровой день."""
    result = await session.execute(
        select(DailyNomination).where(
            DailyNomination.nomination_type == nomination_type,
            DailyNomination.nomination_date == nomination_date,
        )
    )
    return result.scalars().first()
