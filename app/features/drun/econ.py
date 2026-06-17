"""Экономическая власть Тёмного друна — налог/подачка с ЖЁСТКИМИ лимитами.

Друн может в рофл «обложить налогом» (списать) или «пожалеть» (выдать) игрока.
Это ЕДИНСТВЕННАЯ точка, где AI двигает чужой баланс, поэтому ВСЕ ограничения
зашиты в код и не зависят от того, что попросила модель:

* сумма ≤ ``econ_max_pct`` от баланса И ≤ ``econ_max_abs`` (что меньше);
* налог никогда не уводит баланс в ноль/минус (``allow_negative=False``);
* кулдаун на одного игрока (``econ_cooldown_sec``);
* дневной лимит операций на весь чат (``econ_daily_cap``);
* всё пишется в ``transactions`` (reason=drun_tax/drun_grant) и ``world_events``
  для прозрачности и отката.

Деньги двигаются ТОЛЬКО через :func:`app.services.economy.change_balance`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun.config import AiConfig
from app.models import Transaction, User
from app.services import cooldowns, economy, world_events

logger = get_logger(__name__)

REASON_TAX = "drun_tax"
REASON_GRANT = "drun_grant"
_COOLDOWN_ACTION = "drun_econ"
# Нижний предел, чтобы операция вообще имела смысл (не списывать 0-1 ешку).
_MIN_AMOUNT = 5


@dataclass
class EconResult:
    """Итог экономической выходки друна."""

    ok: bool
    kind: str = ""           # "tax" | "grant"
    applied: int = 0         # фактически списано/выдано (положительное число)
    balance: int = 0         # баланс игрока после операции
    reason: str = ""         # причина отказа (для логов), если ok=False


async def _ops_today(session: AsyncSession) -> int:
    """Сколько эконом-операций друн уже сделал за сутки (налог+подачка)."""
    since = now_utc() - timedelta(days=1)
    total = await session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.reason.in_((REASON_TAX, REASON_GRANT)))
        .where(Transaction.created_at >= since)
    )
    return int(total or 0)


def _clamp_amount(requested: int, balance: int, cfg: AiConfig) -> int:
    """Обрезает запрошенную сумму по лимитам (доля баланса И абсолют)."""
    by_pct = int(balance * max(0.0, cfg.econ_max_pct))
    capped = min(abs(int(requested)), by_pct, max(0, cfg.econ_max_abs))
    return capped


async def apply(
    session: AsyncSession,
    *,
    cfg: AiConfig,
    kind: str,
    target_id: int,
    requested_amount: int,
    note: str = "",
) -> EconResult:
    """Применяет налог/подачку с соблюдением всех предохранителей.

    :param kind: ``"tax"`` (списать) или ``"grant"`` (выдать).
    :param requested_amount: сколько ПРОСИТ модель; будет обрезано лимитами.
    :param note: короткая причина «за что» (для леджера и события).
    """
    if not cfg.econ_enabled:
        return EconResult(ok=False, reason="disabled")
    if kind not in ("tax", "grant"):
        return EconResult(ok=False, reason="bad_kind")

    # Дневной лимит на весь чат.
    if await _ops_today(session) >= cfg.econ_daily_cap:
        return EconResult(ok=False, reason="daily_cap")

    # Кулдаун на конкретного игрока.
    remaining = await cooldowns.get_remaining(session, target_id, _COOLDOWN_ACTION)
    if remaining > 0:
        return EconResult(ok=False, reason="cooldown")

    user = await session.get(User, target_id)
    if user is None:
        return EconResult(ok=False, reason="no_user")
    balance = max(0, int(user.balance))

    # Лимиты по сумме. Для налога доля считается от текущего баланса; для
    # подачки — тоже от баланса игрока (богатому «жалость» больше, но капается
    # абсолютом), а нищему хватит и абсолютного минимума.
    if kind == "tax":
        amount = _clamp_amount(requested_amount, balance, cfg)
        if amount < _MIN_AMOUNT:
            return EconResult(ok=False, reason="too_small")
        signed = -amount
        reason = REASON_TAX
        event_type = world_events.EVENT_DRUN_TAX
    else:  # grant
        # Для подачки доля считается от абсолютного лимита, чтобы нищий получил
        # ощутимое, а не процент от нуля.
        amount = min(max(abs(int(requested_amount)), _MIN_AMOUNT), max(0, cfg.econ_max_abs))
        if amount < _MIN_AMOUNT:
            return EconResult(ok=False, reason="too_small")
        signed = amount
        reason = REASON_GRANT
        event_type = world_events.EVENT_DRUN_GRANT

    meta = {"kind": kind, "note": note[:200], "requested": int(requested_amount)}
    try:
        updated = await economy.change_balance(
            session, target_id, signed, reason, meta, allow_negative=False
        )
    except economy.InsufficientFunds:
        return EconResult(ok=False, reason="insufficient")

    await cooldowns.set_cooldown(
        session, target_id, _COOLDOWN_ACTION, cfg.econ_cooldown_sec
    )
    await world_events.emit_safe(
        session,
        type=event_type,
        actor_id=target_id,
        amount=amount,
        meta=meta,
    )
    logger.info(
        "drun econ %s: user=%s amount=%s note=%r -> balance=%s",
        kind, target_id, amount, note[:80], updated.balance,
    )
    return EconResult(
        ok=True, kind=kind, applied=amount, balance=int(updated.balance)
    )
