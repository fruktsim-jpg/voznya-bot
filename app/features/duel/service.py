"""Логика дуэлей.

Сценарий из двух шагов:
1. ``/бой @user ставка`` — создаёт вызов (ставка пока не списывается);
2. ``/го`` — соперник принимает, ставки списываются с обоих, случайно
   определяется победитель, он забирает банк.

Кулдаун ставится только на инициатора при создании вызова.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import User
from app.models.pending_action import (
    STATUS_ACCEPTED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    TYPE_DUEL,
    PendingAction,
)
from app.services import cooldowns
from app.services.economy import change_balance
from app.settings import balance


@dataclass
class ChallengeResult:
    """Результат создания вызова на дуэль."""

    status: str  # "cooldown" / "poor" / "ok"
    remaining: float = 0.0
    balance: int = 0


@dataclass
class DuelResult:
    """Результат проведённой дуэли."""

    status: str  # "no_pending" / "target_poor" / "initiator_poor" / "done"
    balance: int = 0
    winner_id: int = 0
    loser_id: int = 0
    bank: int = 0
    amount: int = 0


async def create_challenge(
    session: AsyncSession,
    initiator_id: int,
    target_id: int,
    amount: int,
    chat_id: int,
) -> ChallengeResult:
    """Создаёт вызов на дуэль от инициатора к цели."""
    remaining = await cooldowns.get_remaining(session, initiator_id, "duel")
    if remaining > 0:
        return ChallengeResult(status="cooldown", remaining=remaining)

    initiator = await session.get(User, initiator_id)
    if initiator is None or initiator.balance < amount:
        return ChallengeResult(
            status="poor", balance=initiator.balance if initiator else 0
        )

    expires_at = now_utc() + timedelta(minutes=balance.DUEL_EXPIRE_MINUTES)
    session.add(
        PendingAction(
            action_type=TYPE_DUEL,
            initiator_id=initiator_id,
            target_id=target_id,
            amount=amount,
            chat_id=chat_id,
            status=STATUS_PENDING,
            expires_at=expires_at,
        )
    )
    await cooldowns.set_cooldown(session, initiator_id, "duel", balance.COOLDOWNS["duel"])
    return ChallengeResult(status="ok")


async def accept_challenge(session: AsyncSession, confirmer_id: int) -> DuelResult:
    """Принимает вызов на дуэль и проводит бой."""
    now = now_utc()
    result = await session.execute(
        select(PendingAction)
        .where(
            PendingAction.action_type == TYPE_DUEL,
            PendingAction.target_id == confirmer_id,
            PendingAction.status == STATUS_PENDING,
        )
        .order_by(PendingAction.created_at.desc())
        .with_for_update()
    )
    pending = result.scalars().first()
    if pending is None:
        return DuelResult(status="no_pending")
    if pending.expires_at <= now:
        pending.status = STATUS_EXPIRED
        return DuelResult(status="no_pending")

    amount = pending.amount or 0
    initiator_id = pending.initiator_id

    # Блокируем строки обоих игроков в порядке возрастания ID (анти-дедлок).
    first_id, second_id = sorted([initiator_id, confirmer_id])
    await session.get(User, first_id, with_for_update=True)
    await session.get(User, second_id, with_for_update=True)

    initiator = await session.get(User, initiator_id)
    confirmer = await session.get(User, confirmer_id)
    assert initiator is not None and confirmer is not None

    if confirmer.balance < amount:
        return DuelResult(status="target_poor", balance=confirmer.balance)
    if initiator.balance < amount:
        pending.status = STATUS_EXPIRED
        return DuelResult(status="initiator_poor")

    # Списываем ставки с обоих.
    await change_balance(session, initiator_id, -amount, "duel", {"role": "stake"})
    await change_balance(session, confirmer_id, -amount, "duel", {"role": "stake"})

    bank = amount * 2
    winner_id, loser_id = random.sample([initiator_id, confirmer_id], 2)

    await change_balance(
        session, winner_id, bank, "duel", {"role": "win", "bank": bank}
    )

    winner = await session.get(User, winner_id)
    loser = await session.get(User, loser_id)
    assert winner is not None and loser is not None
    winner.duels_won += 1
    loser.duels_lost += 1

    pending.status = STATUS_ACCEPTED

    return DuelResult(
        status="done",
        winner_id=winner_id,
        loser_id=loser_id,
        bank=bank,
        amount=amount,
    )
