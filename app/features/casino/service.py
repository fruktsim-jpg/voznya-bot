"""Логика команды /казино."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services import cooldowns
from app.services.economy import change_balance
from app.settings import balance


@dataclass
class CasinoResult:
    """Результат игры в казино."""

    status: str  # "cooldown" / "not_enough" / "done"
    remaining: float = 0.0
    bet: int = 0
    multiplier: float = 0.0
    payout: int = 0
    net: int = 0
    balance: int = 0
    outcome: str = ""


def _pick_outcome() -> dict:
    """Случайно выбирает исход казино согласно весам из настроек."""
    weights = [o["weight"] for o in balance.CASINO_OUTCOMES]
    return random.choices(balance.CASINO_OUTCOMES, weights=weights, k=1)[0]


async def play_casino(session: AsyncSession, user_id: int, bet: int) -> CasinoResult:
    """Проводит ставку в казино.

    Кулдаун ставится только при фактической игре (когда ставка принята).
    """
    remaining = await cooldowns.get_remaining(session, user_id, "casino")
    if remaining > 0:
        return CasinoResult(status="cooldown", remaining=remaining)

    user = await session.get(User, user_id, with_for_update=True)
    assert user is not None
    if user.balance < bet:
        return CasinoResult(status="not_enough", balance=user.balance)

    outcome = _pick_outcome()
    multiplier = float(outcome["multiplier"])
    payout = math.floor(bet * multiplier)
    net = payout - bet

    await change_balance(
        session,
        user_id,
        net,
        reason="casino",
        meta={"bet": bet, "multiplier": multiplier, "payout": payout, "outcome": outcome["name"]},
    )
    user.casino_games_count += 1
    await cooldowns.set_cooldown(session, user_id, "casino", balance.COOLDOWNS["casino"])

    return CasinoResult(
        status="done",
        bet=bet,
        multiplier=multiplier,
        payout=payout,
        net=net,
        balance=user.balance,
        outcome=outcome["name"],
    )
