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
from datetime import datetime, timedelta


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import User
from app.models.pending_action import (
    STATUS_ACCEPTED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    TYPE_DUEL,
    PendingAction,
)

from app.services import cooldowns
from app.services.economy import change_balance
from app.settings import balance
from app.settings import mmr as mmr_settings


@dataclass
class ChallengeResult:
    """Результат создания вызова на дуэль."""

    status: str  # "cooldown" / "poor" / "ok"
    remaining: float = 0.0
    balance: int = 0
    pending_id: int = 0
    # Момент протухания вызова — хендлер по нему планирует автоудаление
    # сообщения с кнопками, если бой так и не приняли/не отклонили.
    expires_at: datetime | None = None



@dataclass
class DuelResult:
    """Результат проведённой дуэли."""

    status: str  # "no_pending"/"not_target"/"target_poor"/"initiator_poor"/"done"
    balance: int = 0
    winner_id: int = 0
    loser_id: int = 0
    bank: int = 0
    amount: int = 0
    # Повышения ранга по итогу дуэли (если случились) — для уведомления.
    winner_rankup: mmr_settings.Rank | None = None
    loser_rankup: mmr_settings.Rank | None = None
    winner_mmr_before: int = 0
    loser_mmr_before: int = 0


async def create_challenge(
    session: AsyncSession,
    initiator_id: int,
    target_id: int | None,
    amount: int,
    chat_id: int,
) -> ChallengeResult:
    """Создаёт вызов на дуэль от инициатора к цели (или открытый вызов, если target_id=None)."""
    remaining = await cooldowns.get_remaining(session, initiator_id, "duel")
    if remaining > 0:
        return ChallengeResult(status="cooldown", remaining=remaining)

    initiator = await session.get(User, initiator_id)
    if initiator is None or initiator.balance < amount:
        return ChallengeResult(
            status="poor", balance=initiator.balance if initiator else 0
        )

    expires_at = now_utc() + timedelta(minutes=balance.DUEL_EXPIRE_MINUTES)
    pending = PendingAction(
        action_type=TYPE_DUEL,
        initiator_id=initiator_id,
        target_id=target_id,
        amount=amount,
        chat_id=chat_id,
        status=STATUS_PENDING,
        expires_at=expires_at,
    )
    session.add(pending)
    await session.flush()
    # ВАЖНО: кулдаун здесь НЕ ставим. Вызов — это ещё не состоявшийся бой:
    # его могут отклонить или просто проигнорировать (никто не примет). Кулдаун
    # начисляется только когда механика реально запустилась — в accept_challenge
    # после успешного боя. Так отказ/просрочка не «съедают» кулдаун инициатора.
    return ChallengeResult(status="ok", pending_id=pending.id, expires_at=expires_at)



async def accept_challenge(
    session: AsyncSession, confirmer_id: int, pending_id: int | None = None
) -> DuelResult:
    """Принимает вызов на дуэль и проводит бой.

    Если ``pending_id`` указан (приём через кнопку), берётся конкретный вызов.
    Для открытых вызовов (target_id=NULL) принять может любой, кроме инициатора.
    Для персональных вызовов проверяется, что нажавший — именно тот, кого вызвали.
    """
    now = now_utc()
    if pending_id is not None:
        pending = await session.get(PendingAction, pending_id, with_for_update=True)
        if pending is None or pending.action_type != TYPE_DUEL or pending.status != STATUS_PENDING:
            return DuelResult(status="no_pending")
        # Для открытых вызовов (target_id=NULL) проверяем, что принимающий не инициатор
        if pending.target_id is None:
            if pending.initiator_id == confirmer_id:
                return DuelResult(status="not_target")
        # Для персональных вызовов проверяем, что принимающий — именно цель
        elif pending.target_id != confirmer_id:
            return DuelResult(status="not_target")
    else:
        # Команда /го ищет последний вызов для этого пользователя
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
    winner.duel_loss_streak = 0
    loser.duels_lost += 1
    loser.duel_loss_streak += 1

    # MMR за дуэль (отдельный игровой рейтинг, не связан с банком/ешками):
    # оба получают за участие, победитель — ещё и за победу. award_mmr вернёт
    # новый ранг, если начисление подняло игрока на следующую ступень.
    #
    # АНТИ-ДУЭЛЬ-ФАРМ: MMR за УЧАСТИЕ начисляется ограниченное число раз в день
    # и только с РАЗНЫМИ оппонентами (повтор с тем же оппонентом или превышение
    # дневного лимита участия → 0 MMR за участие). Победа (MMR_DUEL_WIN) при
    # этом всё равно засчитывается — она требует реального исхода и капается
    # кулдауном дуэли. Оппонент кодируется в reason для проверки «разные».
    from app.features.mmr.service import award_mmr

    winner_part = await _participation_mmr(session, winner_id, loser_id)
    loser_part = await _participation_mmr(session, loser_id, winner_id)
    from app.repositories import mmr as mmr_repo

    winner_mmr_before = await mmr_repo.get_mmr(session, winner_id)
    loser_mmr_before = await mmr_repo.get_mmr(session, loser_id)

    winner_rankup = await award_mmr(
        session,
        player_id=winner_id,
        amount=winner_part + mmr_settings.MMR_DUEL_WIN,
        source=mmr_settings.SOURCE_DUEL,
        reason=f"win:{loser_id}",
    )
    loser_rankup = await award_mmr(
        session,
        player_id=loser_id,
        amount=loser_part,
        source=mmr_settings.SOURCE_DUEL,
        reason=f"participation:{winner_id}",
    )

    # Прогресс недельной миссии «выиграй N дуэлей» (если идёт сезон).
    from app.features.season.service import progress_mission
    from app.settings import season as season_cfg

    await progress_mission(
        session,
        user_id=winner_id,
        metric=season_cfg.MISSION_METRIC_DUEL_WIN,
    )


    pending.status = STATUS_ACCEPTED

    # Кулдаун дуэли ставится здесь — только теперь бой реально состоялся.
    # Обоим участникам, чтобы спам-замесами не заваливали чат.
    await cooldowns.set_cooldown(session, initiator_id, "duel", balance.COOLDOWNS["duel"])
    await cooldowns.set_cooldown(session, confirmer_id, "duel", balance.COOLDOWNS["duel"])

    return DuelResult(

        status="done",
        winner_id=winner_id,
        loser_id=loser_id,
        bank=bank,
        amount=amount,
        winner_rankup=winner_rankup,
        loser_rankup=loser_rankup,
        winner_mmr_before=winner_mmr_before,
        loser_mmr_before=loser_mmr_before,
    )


@dataclass
class DeclineResult:
    """Результат отказа от вызова на дуэль."""

    status: str  # "no_pending" / "not_target" / "ok"
    initiator_id: int = 0
    decliner_id: int = 0


async def decline_challenge(
    session: AsyncSession, decliner_id: int, pending_id: int
) -> DeclineResult:
    """Отклоняет вызов на дуэль и закрывает запись.

    Отказаться может только тот, кого вызвали (для персонального вызова) или
    любой, кроме инициатора (для открытого вызова). Ставки не списывались,
    поэтому возвращать ничего не нужно — просто помечаем вызов отклонённым.
    """
    pending = await session.get(PendingAction, pending_id, with_for_update=True)
    if (
        pending is None
        or pending.action_type != TYPE_DUEL
        or pending.status != STATUS_PENDING
    ):
        return DeclineResult(status="no_pending")

    if pending.target_id is None:
        # Открытый вызов: инициатор не может «слиться» за других.
        if pending.initiator_id == decliner_id:
            return DeclineResult(status="not_target")
    elif pending.target_id != decliner_id:
        return DeclineResult(status="not_target")

    pending.status = STATUS_DECLINED
    return DeclineResult(
        status="ok",
        initiator_id=pending.initiator_id,
        decliner_id=decliner_id,
    )


async def _participation_mmr(
    session: AsyncSession, player_id: int, opponent_id: int
) -> int:
    """MMR за участие в дуэли с учётом анти-фарма.

    Возвращает ``MMR_DUEL_PARTICIPATION``, только если за сегодня (UTC) игрок
    ещё не превысил дневной лимит начислений за дуэли И ещё не дрался с этим
    оппонентом. Иначе 0 (победа всё равно начисляется отдельно).
    """
    from app.repositories import season as season_repo
    from app.settings import season as season_cfg

    day_start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    count, opponents = await season_repo.duel_mmr_grants_today(
        session, player_id=player_id, since=day_start
    )
    if count >= season_cfg.DUEL_MMR_PARTICIPATION_MAX_PER_DAY:
        return 0
    if opponent_id in opponents:
        return 0
    return mmr_settings.MMR_DUEL_PARTICIPATION


async def expire_challenge_if_pending(
    session: AsyncSession, pending_id: int
) -> bool:

    """Помечает вызов просроченным, если он ещё висит в статусе pending.

    Возвращает True, если вызов был именно просрочен этим вызовом (т.е. его
    никто не принял/не отклонил). Возвращает False, если вызов уже разрулен
    (принят, отклонён, просрочен ранее) или не существует — в этом случае
    чистить сообщения в чате не нужно.
    """
    pending = await session.get(PendingAction, pending_id, with_for_update=True)
    if (
        pending is None
        or pending.action_type != TYPE_DUEL
        or pending.status != STATUS_PENDING
    ):
        return False
    pending.status = STATUS_EXPIRED
    return True


