"""Структурированные автономные ивенты друна (Phase 4).

Друн перестаёт только КОММЕНТИРОВАТЬ и начинает СОЗДАВАТЬ движ: челленджи,
прогнозы, мини-ивенты, временные цели — с участниками, наградой, дедлайном и
исходом. Это объекты со своим жизненным циклом (``drun_events``), а не просто
текст в чат.

Жизненный цикл:
    proposed → active → resolved
                      ↘ cancelled

Деньги (награды) двигаются ТОЛЬКО через экономическое ядро бота
(``economy.change_balance``, Model 2) — таблица ``drun_events`` лишь описывает
ивент и хранит состояние. Выплата при resolve пишет ``transactions`` (reason=
``drun_event``) и проецируется в ``world_events`` (друн «видит» свой же ивент).

Безопасность: награды клампятся (``_MAX_REWARD``), один игрок не записывается
дважды (дедуп по id), resolve идемпотентен (только active→resolved).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.models import DrunEvent
from app.services import economy

logger = get_logger(__name__)

# Виды ивентов.
KIND_CHALLENGE = "challenge"      # «первый, кто сделает X, получит N»
KIND_PREDICTION = "prediction"    # «угадай исход — призовой фонд делится»
KIND_MINI_EVENT = "mini_event"    # тематический движ (счастливый час и т.п.)
KIND_GOAL = "goal"                # временная цель чата

# Статусы.
STATUS_PROPOSED = "proposed"
STATUS_ACTIVE = "active"
STATUS_RESOLVED = "resolved"
STATUS_CANCELLED = "cancelled"

# Предохранители: награда за ивент ограничена (друн не печатает экономику).
_MAX_REWARD = 5000
_MAX_ACTIVE = 3            # сколько ивентов друна может идти одновременно
_DEFAULT_TTL_HOURS = 6

REASON_EVENT = "drun_event"


@dataclass
class EventResult:
    ok: bool
    event_id: int = 0
    error: str = ""


def _clamp_reward(amount: int | None) -> int:
    if not amount or amount <= 0:
        return 0
    return min(int(amount), _MAX_REWARD)


async def active_count(session: AsyncSession) -> int:
    """Сколько ивентов друна сейчас активно (для лимита одновременных)."""
    rows = await session.execute(
        select(DrunEvent.id).where(DrunEvent.status == STATUS_ACTIVE)
    )
    return len(rows.all())


async def create_event(
    session: AsyncSession,
    *,
    kind: str,
    title: str,
    body: str = "",
    created_by: int | None = None,
    reward_amount: int | None = None,
    ttl_hours: int = _DEFAULT_TTL_HOURS,
    meta: dict | None = None,
) -> EventResult:
    """Создаёт активный ивент. Commit — на вызывающем.

    Возвращает ``ok=False, error='too_many'``, если уже идёт максимум активных
    ивентов — друн не должен заваливать чат параллельными движами.
    """
    if await active_count(session) >= _MAX_ACTIVE:
        return EventResult(ok=False, error="too_many")
    title = (title or "").strip()[:256]
    if not title:
        return EventResult(ok=False, error="empty_title")
    reward = _clamp_reward(reward_amount)
    deadline = now_utc() + timedelta(hours=max(1, min(72, ttl_hours)))
    ev = DrunEvent(
        kind=kind,
        status=STATUS_ACTIVE,
        title=title,
        body=(body or "").strip()[:2000],
        created_by=created_by,
        reward_kind="eshki" if reward > 0 else None,
        reward_amount=reward or None,
        participants=[],
        deadline_at=deadline,
        meta=meta or {},
    )
    session.add(ev)
    await session.flush()
    return EventResult(ok=True, event_id=ev.id)


async def join_event(
    session: AsyncSession, *, event_id: int, user_id: int, choice: str | None = None
) -> EventResult:
    """Записывает игрока в ивент (идемпотентно). ``choice`` — для прогнозов."""
    ev = await session.get(DrunEvent, event_id)
    if ev is None:
        return EventResult(ok=False, error="not_found")
    if ev.status != STATUS_ACTIVE:
        return EventResult(ok=False, error="not_active")
    if ev.deadline_at is not None and ev.deadline_at <= now_utc():
        return EventResult(ok=False, error="expired")

    participants = list(ev.participants or [])
    if any(p.get("id") == user_id for p in participants):
        return EventResult(ok=False, event_id=event_id, error="already_joined")
    entry = {"id": user_id, "joined_at": now_utc().isoformat()}
    if choice:
        entry["choice"] = str(choice)[:64]
    participants.append(entry)
    # JSONB-поле переприсваиваем целиком (in-place изменение SQLAlchemy не ловит).
    ev.participants = participants
    ev.updated_at = now_utc()
    return EventResult(ok=True, event_id=event_id)


async def list_active(session: AsyncSession, *, limit: int = 10) -> list[DrunEvent]:
    """Активные ивенты, свежие сверху."""
    rows = await session.execute(
        select(DrunEvent)
        .where(DrunEvent.status == STATUS_ACTIVE)
        .order_by(DrunEvent.created_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def due_events(session: AsyncSession, *, limit: int = 20) -> list[DrunEvent]:
    """Активные ивенты с истёкшим дедлайном — пора разрешить (для планировщика)."""
    rows = await session.execute(
        select(DrunEvent)
        .where(DrunEvent.status == STATUS_ACTIVE)
        .where(DrunEvent.deadline_at.is_not(None))
        .where(DrunEvent.deadline_at <= now_utc())
        .order_by(DrunEvent.deadline_at.asc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def resolve_event(
    session: AsyncSession,
    *,
    event_id: int,
    winner_ids: list[int] | None = None,
    outcome: dict | None = None,
    correct_choice: str | None = None,
) -> EventResult:
    """Разрешает ивент: помечает resolved, выплачивает награду победителям.

    Идемпотентно: только ``active`` → ``resolved``.

    Выбор победителей:
    * ``winner_ids`` задан явно — платим им;
    * иначе для ПРОГНОЗА (``KIND_PREDICTION``) с известным ``correct_choice`` —
      победители те, чей ``choice`` совпал с правильным исходом (угадайка не
      платит всем подряд);
    * иначе — все участники (челлендж/мини-ивент/цель).

    Награда (если задана) делится РОВНО на призовой фонд: каждому
    ``reward_total // n``, остаток — первому победителю. Никто не получает
    «доплату до 1», поэтому суммарная выплата НИКОГДА не превышает объявленный
    (и заклампленный при создании) фонд. Выплата проецируется в
    ``world_events``. Commit — на вызывающем.
    """
    ev = await session.get(DrunEvent, event_id)
    if ev is None:
        return EventResult(ok=False, error="not_found")
    if ev.status != STATUS_ACTIVE:
        return EventResult(ok=False, event_id=event_id, error="not_active")

    participants = ev.participants or []
    if winner_ids is None:
        if ev.kind == KIND_PREDICTION and correct_choice is not None:
            # Прогноз: платим только угадавшим (choice == правильный исход).
            target = str(correct_choice).strip().lower()
            winner_ids = [
                p.get("id")
                for p in participants
                if p.get("id") and str(p.get("choice", "")).strip().lower() == target
            ]
        else:
            winner_ids = [p.get("id") for p in participants if p.get("id")]
    winner_ids = [w for w in dict.fromkeys(winner_ids) if w]  # дедуп, без None

    paid: list[dict] = []
    reward_total = int(ev.reward_amount or 0)
    n = len(winner_ids)
    if reward_total > 0 and n > 0:
        per = reward_total // n
        if per > 0:
            remainder = reward_total - per * n  # остаток — первому победителю
            for idx, uid in enumerate(winner_ids):
                amount = per + (remainder if idx == 0 else 0)
                try:
                    user = await economy.change_balance(
                        session, uid, amount, REASON_EVENT,
                        {"event_id": event_id, "kind": ev.kind, "title": ev.title},
                    )
                    paid.append({"id": uid, "delta": amount, "balance": user.balance})
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "drun_event payout failed (event=%s user=%s)", event_id, uid,
                        exc_info=True,
                    )
        # per == 0 (фонд меньше числа победителей): не «доплачиваем до 1», иначе
        # выплата превысила бы фонд. Награда просто не делится — ивент закрыт.

    ev.status = STATUS_RESOLVED
    ev.resolved_at = now_utc()
    ev.updated_at = now_utc()
    ev.outcome = {
        **(outcome or {}),
        "winners": winner_ids,
        "paid": paid,
    }

    # Проекция в world_events: друн «видит» исход своего ивента как новость мира.
    try:
        from app.services import world_events

        await world_events.emit_safe(
            session,
            type=world_events.EVENT_DRUN_EVENT_RESOLVED,
            actor_id=ev.created_by,
            amount=reward_total or None,
            ref_table="drun_events",
            ref_id=event_id,
            severity=2 if reward_total > 0 else 1,
            meta={
                "kind": ev.kind,
                "title": ev.title,
                "winners": winner_ids,
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("drun_event resolve emit failed", exc_info=True)

    return EventResult(ok=True, event_id=event_id)


async def cancel_event(session: AsyncSession, *, event_id: int) -> EventResult:
    """Отменяет ивент (active/proposed → cancelled). Без выплат."""
    ev = await session.get(DrunEvent, event_id)
    if ev is None:
        return EventResult(ok=False, error="not_found")
    if ev.status not in (STATUS_ACTIVE, STATUS_PROPOSED):
        return EventResult(ok=False, event_id=event_id, error="not_cancellable")
    ev.status = STATUS_CANCELLED
    ev.updated_at = now_utc()
    return EventResult(ok=True, event_id=event_id)


# --- Планировщик авто-разрешения дозревших ивентов ---------------------------


async def resolve_due(session: AsyncSession) -> list[tuple[int, str]]:
    """Разрешает все ивенты с истёкшим дедлайном.

    Возвращает [(event_id, title), ...] разрешённых — чтобы вызывающий мог
    объявить итог в чат. Прогнозы без явных победителей платят всем участникам
    (равная доля); челлендж без записавшихся просто закрывается без выплат.
    """
    resolved: list[tuple[int, str]] = []
    for ev in await due_events(session):
        title = ev.title
        res = await resolve_event(session, event_id=ev.id)
        if res.ok:
            resolved.append((ev.id, title))
    return resolved


def setup_events_scheduler(
    scheduler,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    minutes: int = 5,
) -> None:
    """Регистрирует фоновое авто-разрешение дозревших ивентов друна.

    Раз в ``minutes`` минут закрывает ивенты с истёкшим дедлайном, выплачивает
    награды через экономическое ядро и (если есть Presence) объявляет итог в
    группу голосом друна. Любой сбой — тихий лог, мир не падает.
    """

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                resolved = await resolve_due(session)
                await session.commit()
            if not resolved:
                return
            from app.features.drun.presence import get_presence

            presence = get_presence()
            if presence is None:
                return
            for _eid, title in resolved:
                await presence.announce(
                    f"Ивент завершён: {title}. Итоги подведены.",
                    kind="event_result",
                )
        except Exception:  # noqa: BLE001
            logger.warning("drun events resolver failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_events_resolve",
        replace_existing=True,
    )

