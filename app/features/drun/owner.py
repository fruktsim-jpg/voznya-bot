"""Owner intelligence (Phase 6): предпочтения владельца + approval-flow.

Друн перестаёт быть «рукой, которая жмёт кнопку по команде» и становится
ОПЕРАТОРОМ-ПОМОЩНИКОМ:

* **Память предпочтений** — друн помнит, КАК владелец любит, чтобы вещи
  делались («инфляцию гаси налогом, а не урезанием фарма», «новичков не
  баню — мьют на час»). Хранится в ``ai_memories`` (kind=``owner_pref``,
  subject_id=owner_id), подмешивается в планирование/рекомендации.
* **Approval-flow** — для высокоимпактных действий (массовые выдачи, перм-бан,
  глобальные эконом-сдвиги) друн НЕ исполняет сразу: он создаёт
  ``drun_proposals`` (pending) и просит владельца подтвердить. Владелец
  одобряет → система исполняет тем же registry-диспетчером. Малоимпактные
  действия по-прежнему идут напрямую (в пределах капов).

Безопасность: предложение хранит сериализованный tool-вызов; исполнение всё
равно проходит через ``registry.dispatch`` с его клампами и аудитом. Никакого
обхода Model 2 — деньги двигает экономическое ядро бота.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.models import AiMemory, DrunProposal

logger = get_logger(__name__)

# Срок жизни pending-предложения: дольше — теряет актуальность (данные мира ушли).
_PROPOSAL_TTL_HOURS = 24
# Сколько предпочтений владельца держим (свежие/частые важнее).
_MAX_PREFS = 24

OWNER_PREF_KIND = "owner_pref"

# Высокоимпактные инструменты: их друн не исполняет без подтверждения владельца,
# даже если он сам же попросил «в общих словах». Малые/обратимые — мимо очереди.
HIGH_IMPACT_TOOLS = frozenset(
    {
        "grant",            # массовая выдача по аудитории
        "giveaway",         # розыгрыш крупного пула
        "ban",              # бан (особенно перманентный)
        "multiplier",       # глобальный множитель экономики
        "set_setting",      # правка параметров мира
    }
)


# --- Память предпочтений владельца -------------------------------------------


async def remember_preference(
    session: AsyncSession, *, owner_id: int, text: str, weight: int = 2
) -> None:
    """Сохраняет предпочтение владельца (как друн должен действовать).

    Дедуп по точному тексту: повторное «правило» лишь поднимает вес/свежесть,
    а не плодит дубликаты. Commit — на вызывающем.
    """
    fact = (text or "").strip()[:300]
    if not fact:
        return
    existing = (
        await session.execute(
            select(AiMemory).where(
                AiMemory.subject_id == owner_id,
                AiMemory.kind == OWNER_PREF_KIND,
                AiMemory.fact == fact,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.weight = min(3, (existing.weight or 1) + 1)
        existing.updated_at = now_utc()
        return
    session.add(
        AiMemory(
            subject_id=owner_id,
            kind=OWNER_PREF_KIND,
            fact=fact,
            weight=weight,
            source="owner",
        )
    )
    await _prune_prefs(session, owner_id)


async def _prune_prefs(session: AsyncSession, owner_id: int) -> None:
    """Держит не больше ``_MAX_PREFS`` предпочтений — вытесняет слабые/старые."""
    rows = (
        await session.execute(
            select(AiMemory)
            .where(
                AiMemory.subject_id == owner_id,
                AiMemory.kind == OWNER_PREF_KIND,
            )
            .order_by(AiMemory.weight.desc(), AiMemory.updated_at.desc())
        )
    ).scalars().all()
    for extra in rows[_MAX_PREFS:]:
        await session.delete(extra)


async def preferences_block(session: AsyncSession, owner_id: int) -> str:
    """Текстовый блок предпочтений владельца для подмешивания в промпт."""
    rows = (
        await session.execute(
            select(AiMemory.fact)
            .where(
                AiMemory.subject_id == owner_id,
                AiMemory.kind == OWNER_PREF_KIND,
            )
            .order_by(AiMemory.weight.desc(), AiMemory.updated_at.desc())
            .limit(12)
        )
    ).all()
    facts = [r[0] for r in rows if r[0]]
    if not facts:
        return ""
    lines = ["# КАК ВЛАДЕЛЕЦ ЛЮБИТ, ЧТОБЫ ТЫ ДЕЙСТВОВАЛ (его правила):"]
    lines += [f"- {f}" for f in facts]
    return "\n".join(lines)


# --- Approval-flow: предложения действий -------------------------------------


def is_high_impact(tool: str) -> bool:
    """Нужно ли подтверждение владельца для этого инструмента."""
    return tool in HIGH_IMPACT_TOOLS


async def create_proposal(
    session: AsyncSession,
    *,
    owner_id: int,
    tool: str,
    args: dict,
    rationale: str,
) -> DrunProposal:
    """Создаёт pending-предложение действия. Commit — на вызывающем."""
    proposal = DrunProposal(
        status="pending",
        owner_id=owner_id,
        tool=tool,
        args=args or {},
        rationale=(rationale or "")[:1000],
        expires_at=now_utc() + timedelta(hours=_PROPOSAL_TTL_HOURS),
    )
    session.add(proposal)
    await session.flush()
    return proposal


async def pending_proposals(
    session: AsyncSession, *, owner_id: int | None = None, limit: int = 10
) -> list[DrunProposal]:
    """Свежие pending-предложения (для показа владельцу), новые сверху."""
    await expire_stale(session)
    stmt = (
        select(DrunProposal)
        .where(DrunProposal.status == "pending")
        .order_by(DrunProposal.created_at.desc())
        .limit(limit)
    )
    if owner_id is not None:
        stmt = stmt.where(DrunProposal.owner_id == owner_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_proposal(session: AsyncSession, proposal_id: int) -> DrunProposal | None:
    """Возвращает предложение по id, ЛЕНИВО протухая его при истёкшем TTL.

    Важно для безопасности: «да N» по конкретному номеру не проходит через
    ``pending_proposals``/``latest_pending`` (которые свипают expired), поэтому
    без этой проверки протухшее высокоимпактное предложение можно было бы
    исполнить по прямому id мимо 24h-окна. Здесь — single source of truth: любой
    путь, читающий предложение по id, видит его уже expired, если срок вышел.
    """
    proposal = await session.get(DrunProposal, proposal_id)
    if (
        proposal is not None
        and proposal.status == "pending"
        and proposal.expires_at is not None
        and proposal.expires_at <= now_utc()
    ):
        proposal.status = "expired"
    return proposal


async def latest_pending(
    session: AsyncSession, *, owner_id: int | None = None
) -> DrunProposal | None:
    """Самое свежее pending-предложение (для «друн, да» без номера)."""
    items = await pending_proposals(session, owner_id=owner_id, limit=1)
    return items[0] if items else None


async def mark_decided(
    session: AsyncSession,
    proposal: DrunProposal,
    *,
    status: str,
    decided_by: int,
    result: dict | None = None,
) -> None:
    """Помечает предложение решённым (approved/rejected/executed). Commit — снаружи."""
    proposal.status = status
    proposal.decided_by = decided_by
    proposal.decided_at = now_utc()
    if result is not None:
        proposal.result = result


async def expire_stale(session: AsyncSession) -> int:
    """Помечает протухшие pending-предложения expired. Возвращает число."""
    rows = (
        await session.execute(
            select(DrunProposal).where(
                DrunProposal.status == "pending",
                DrunProposal.expires_at.is_not(None),
                DrunProposal.expires_at <= now_utc(),
            )
        )
    ).scalars().all()
    for p in rows:
        p.status = "expired"
    return len(rows)
