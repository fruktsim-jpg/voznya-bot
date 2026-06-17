"""Логика браков: предложение, подтверждение, отказ, информация, развод.

Правила:
* у одного пользователя может быть только один активный брак;
* разрешены любые пары;
* свадьба требует подтверждения второй стороной;
* развод мгновенный (без подтверждения).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import Marriage
from app.models.pending_action import (
    STATUS_ACCEPTED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    TYPE_DIVORCE,
    TYPE_MARRY,
    PendingAction,
)
from app.repositories import marriages as marriages_repo
from app.settings import balance


@dataclass
class MarrySimpleResult:
    status: str
    partner_id: int = 0
    pending_id: int = 0


@dataclass
class AcceptResult:
    status: str  # "no_pending"/"not_target"/"initiator_busy"/"target_busy"/"done"
    initiator_id: int = 0
    target_id: int = 0


async def propose(
    session: AsyncSession, initiator_id: int, target_id: int, chat_id: int
) -> MarrySimpleResult:
    """Создаёт предложение руки и сердца."""
    if await marriages_repo.get_active_marriage(session, initiator_id):
        return MarrySimpleResult(status="initiator_busy")
    if await marriages_repo.get_active_marriage(session, target_id):
        return MarrySimpleResult(status="target_busy", partner_id=target_id)

    expires_at = now_utc() + timedelta(minutes=balance.MARRIAGE_PROPOSAL_EXPIRE_MINUTES)
    pending = PendingAction(
        action_type=TYPE_MARRY,
        initiator_id=initiator_id,
        target_id=target_id,
        chat_id=chat_id,
        status=STATUS_PENDING,
        expires_at=expires_at,
    )
    session.add(pending)
    await session.flush()
    return MarrySimpleResult(status="ok", partner_id=target_id, pending_id=pending.id)


async def accept_proposal(
    session: AsyncSession, confirmer_id: int, pending_id: int | None = None
) -> AcceptResult:
    """Подтверждает предложение и создаёт брак."""
    now = now_utc()
    if pending_id is not None:
        pending = await session.get(PendingAction, pending_id, with_for_update=True)
        if pending is None or pending.action_type != TYPE_MARRY or pending.status != STATUS_PENDING:
            return AcceptResult(status="no_pending")
        if pending.target_id != confirmer_id:
            return AcceptResult(status="not_target")
    else:
        result = await session.execute(
            select(PendingAction)
            .where(
                PendingAction.action_type == TYPE_MARRY,
                PendingAction.target_id == confirmer_id,
                PendingAction.status == STATUS_PENDING,
            )
            .order_by(PendingAction.created_at.desc())
            .with_for_update()
        )
        pending = result.scalars().first()
        if pending is None:
            return AcceptResult(status="no_pending")
    if pending.expires_at <= now:
        pending.status = STATUS_EXPIRED
        return AcceptResult(status="no_pending")

    initiator_id = pending.initiator_id

    # Повторно проверяем, что оба свободны (с блокировкой).
    if await marriages_repo.get_active_marriage(session, initiator_id, lock=True):
        pending.status = STATUS_EXPIRED
        return AcceptResult(status="initiator_busy")
    if await marriages_repo.get_active_marriage(session, confirmer_id, lock=True):
        pending.status = STATUS_EXPIRED
        return AcceptResult(status="target_busy")

    marriage = Marriage(
        user_id_1=initiator_id, user_id_2=confirmer_id, married_at=now_utc()
    )
    session.add(marriage)
    await session.flush()
    pending.status = STATUS_ACCEPTED

    # Событие мира: заключён брак. Та же транзакция.
    from app.services import world_events

    await world_events.emit_safe(
        session,
        type=world_events.EVENT_MARRIAGE_CREATED,
        actor_id=initiator_id,
        target_id=confirmer_id,
        ref_table="marriages",
        ref_id=marriage.id,
    )
    return AcceptResult(status="done", initiator_id=initiator_id, target_id=confirmer_id)


async def decline_proposal(
    session: AsyncSession, decliner_id: int, pending_id: int | None = None
) -> AcceptResult:
    """Отказывает в предложении брака."""
    now = now_utc()
    if pending_id is not None:
        pending = await session.get(PendingAction, pending_id, with_for_update=True)
        if pending is None or pending.action_type != TYPE_MARRY or pending.status != STATUS_PENDING:
            return AcceptResult(status="no_pending")
        if pending.target_id != decliner_id:
            return AcceptResult(status="not_target")
    else:
        result = await session.execute(
            select(PendingAction)
            .where(
                PendingAction.action_type == TYPE_MARRY,
                PendingAction.target_id == decliner_id,
                PendingAction.status == STATUS_PENDING,
            )
            .order_by(PendingAction.created_at.desc())
            .with_for_update()
        )
        pending = result.scalars().first()
        if pending is None:
            return AcceptResult(status="no_pending")
    
    if pending.expires_at <= now:
        pending.status = STATUS_EXPIRED
        return AcceptResult(status="no_pending")

    initiator_id = pending.initiator_id
    pending.status = STATUS_DECLINED
    return AcceptResult(status="done", initiator_id=initiator_id, target_id=decliner_id)


async def get_marriage(session: AsyncSession, user_id: int) -> Marriage | None:
    """Возвращает активный брак пользователя."""
    return await marriages_repo.get_active_marriage(session, user_id)


async def request_divorce(
    session: AsyncSession, initiator_id: int, chat_id: int
) -> MarrySimpleResult:
    """Создаёт запрос на развод."""
    marriage = await marriages_repo.get_active_marriage(session, initiator_id)
    if marriage is None:
        return MarrySimpleResult(status="no_marriage")

    partner_id = (
        marriage.user_id_2 if marriage.user_id_1 == initiator_id else marriage.user_id_1
    )
    expires_at = now_utc() + timedelta(minutes=balance.MARRIAGE_PROPOSAL_EXPIRE_MINUTES)
    session.add(
        PendingAction(
            action_type=TYPE_DIVORCE,
            initiator_id=initiator_id,
            target_id=partner_id,
            chat_id=chat_id,
            status=STATUS_PENDING,
            expires_at=expires_at,
        )
    )
    return MarrySimpleResult(status="ok", partner_id=partner_id)


async def confirm_divorce(session: AsyncSession, confirmer_id: int) -> AcceptResult:
    """Подтверждает развод и завершает брак."""
    now = now_utc()
    result = await session.execute(
        select(PendingAction)
        .where(
            PendingAction.action_type == TYPE_DIVORCE,
            PendingAction.target_id == confirmer_id,
            PendingAction.status == STATUS_PENDING,
        )
        .order_by(PendingAction.created_at.desc())
        .with_for_update()
    )
    pending = result.scalars().first()
    if pending is None or pending.expires_at <= now:
        if pending is not None:
            pending.status = STATUS_EXPIRED
        return AcceptResult(status="no_pending")

    initiator_id = pending.initiator_id
    marriage = await marriages_repo.get_active_marriage(session, confirmer_id, lock=True)
    if marriage is None:
        pending.status = STATUS_EXPIRED
        return AcceptResult(status="no_pending")

    marriage.divorced_at = now_utc()
    pending.status = STATUS_ACCEPTED
    return AcceptResult(status="done", initiator_id=initiator_id, target_id=confirmer_id)
