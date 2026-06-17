"""Логика команды /казино."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services import cooldowns
from app.services.economy import change_balance
from app.settings import balance, dynamic


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
    jackpot: bool = False
    all_in: bool = False


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

    pre_balance = user.balance
    all_in = bet == pre_balance

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

    # Счётчики для секретных достижений.
    if outcome["name"] == "loss":
        user.casino_loss_streak += 1
        if bet > user.max_casino_loss:
            user.max_casino_loss = bet
    else:
        user.casino_loss_streak = 0

    # Кулдаун казино редактируется из админки (app_settings: casino.cooldown);
    # если ключа нет — дефолт из balance.COOLDOWNS.
    casino_cd = await dynamic.get_int(
        session, "casino.cooldown", balance.COOLDOWNS["casino"]
    )
    await cooldowns.set_cooldown(session, user_id, "casino", casino_cd)

    # Событие мира: только крупный выигрыш (порог как в ленте сайта), чтобы не
    # засорять поток рядовыми ставками. Та же транзакция.
    if payout >= 1000 and net > 0:
        from app.services import world_events

        await world_events.emit_safe(
            session,
            type=world_events.EVENT_CASINO_BIG_WIN,
            actor_id=user_id,
            amount=payout,
            meta={
                "bet": bet,
                "payout": payout,
                "net": net,
                "multiplier": multiplier,
                "outcome": outcome["name"],
            },
        )

    return CasinoResult(
        status="done",
        bet=bet,
        multiplier=multiplier,
        payout=payout,
        net=net,
        balance=user.balance,
        outcome=outcome["name"],
        jackpot=outcome["name"] == "jackpot",
        all_in=all_in,
    )
