"""Логика команды /ферма и серии активности.

Серия растёт за использование фермы в разные календарные дни подряд.
Пропуск дня сбрасывает серию. Бонус серии применяется к положительной награде.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import farm_day, now_utc
from app.models import User
from app.services import cooldowns
from app.services.economy import change_balance
from app.settings import balance


@dataclass
class FarmResult:
    """Результат попытки фермы."""

    on_cooldown: bool
    remaining: float = 0.0
    outcome: str = ""
    amount: int = 0
    balance: int = 0
    streak: int = 0
    streak_percent: int = 0


def _compute_streak(user: User, today: date) -> int:
    """Вычисляет новое значение серии после фермы в день ``today``."""
    if user.last_farm_at is None:
        return 1
    last_day = farm_day(user.last_farm_at)
    if last_day == today:
        # Уже фермил(а) сегодня — серия не меняется.
        return user.farm_streak
    if last_day == today - timedelta(days=1):
        return user.farm_streak + 1
    return 1


def _streak_bonus(streak: int) -> float:
    """Возвращает процент бонуса (долей единицы) для текущей серии."""
    bonus = 0.0
    for days, value in sorted(balance.FARM_STREAK_BONUSES.items()):
        if streak >= days:
            bonus = value
    return bonus


def _pick_outcome() -> dict:
    """Случайно выбирает исход фермы согласно весам из настроек."""
    weights = [o["weight"] for o in balance.FARM_OUTCOMES]
    return random.choices(balance.FARM_OUTCOMES, weights=weights, k=1)[0]


async def do_farm(session: AsyncSession, user_id: int) -> FarmResult:
    """Выполняет ферму: проверяет кулдаун, начисляет награду, обновляет серию."""
    remaining = await cooldowns.get_remaining(session, user_id, "farm")
    if remaining > 0:
        return FarmResult(on_cooldown=True, remaining=remaining)

    user = await session.get(User, user_id, with_for_update=True)
    assert user is not None  # пользователь создан в middleware

    today = farm_day()
    new_streak = _compute_streak(user, today)
    bonus = _streak_bonus(new_streak)

    outcome = _pick_outcome()
    base = random.randint(outcome["min"], outcome["max"])

    if base > 0:
        amount = int(round(base * (1 + bonus)))
    elif base < 0:
        # Нельзя уйти в минус — теряем не больше, чем есть на балансе.
        amount = -min(-base, user.balance)
    else:
        amount = 0

    if amount != 0:
        await change_balance(
            session,
            user_id,
            amount,
            reason="farm",
            meta={"outcome": outcome["name"], "base": base, "streak": new_streak},
        )

    user.last_farm_at = now_utc()
    user.farm_streak = new_streak
    if new_streak > user.max_farm_streak:
        user.max_farm_streak = new_streak

    await cooldowns.set_cooldown(session, user_id, "farm", balance.COOLDOWNS["farm"])

    return FarmResult(
        on_cooldown=False,
        outcome=outcome["name"],
        amount=amount,
        balance=user.balance,
        streak=new_streak,
        streak_percent=int(round(bonus * 100)),
    )
