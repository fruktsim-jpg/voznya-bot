"""Инструменты владельца: реальные действия друна над миром Возни.

Владелец пишет в чате обычным языком — «друн дай всем кто писал за час 100
ешек», «друн сбрось всем кд на ферму», «друн разыграй 5000 среди активных» — а
LLM-планировщик (``agent.py``) превращает это в вызов одного из инструментов
отсюда. Здесь только исполнение с жёсткими предохранителями и аудитом.

ГЕЙТ: инструменты вызываются ТОЛЬКО для пользователей из ``ADMIN_IDS``
(эффективный owner). Это проверяет ``agent.py`` ДО планирования; сами функции
тоже не доверяют вводу и клампят масштабы.

Каждый инструмент:
* ограничен предохранителями (лимиты сумм, размер аудитории);
* пишет ``world_events`` и ``audit_log`` (прозрачность и возможность отката);
* возвращает ``ToolResult`` с человекочитаемым итогом для ответа друна.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.money import money
from app.core.utils import now_utc
from app.models import AiMessage, User
from app.services import economy

logger = get_logger(__name__)

# --- Предохранители (жёсткие потолки, чтобы случайно не сжечь экономику) ------
MAX_GRANT_PER_USER = 100_000      # максимум ешек одному за одну операцию
MAX_BULK_USERS = 500              # максимум получателей в одной bulk-операции
MAX_GIVEAWAY_POOL = 5_000_000     # максимум призового фонда розыгрыша
REASON_OWNER = "owner_drun"       # reason для transactions/аудита


@dataclass
class ToolResult:
    """Итог исполнения инструмента."""

    ok: bool
    summary: str = ""                 # короткий человекочитаемый итог
    affected: int = 0                 # сколько игроков затронуто
    error: str = ""
    meta: dict = field(default_factory=dict)


async def _audit(
    session: AsyncSession, actor_id: int, action: str, target_id: int | None,
    reason: str, meta: dict,
) -> None:
    """Пишет строку аудита (мягко: сбой аудита не валит операцию)."""
    try:
        from app.repositories import moderation as mod_repo

        await mod_repo.write_audit(
            session, actor_user_id=actor_id, actor_role="owner",
            action=action, target_user_id=target_id, reason=reason, meta=meta,
        )
    except Exception:  # noqa: BLE001
        logger.debug("tool audit failed", exc_info=True)


async def _audience_recent_chat(
    session: AsyncSession, *, minutes: int, exclude: set[int] | None = None
) -> list[int]:
    """Игроки, писавшие живые реплики за последние ``minutes`` минут."""
    since = now_utc() - timedelta(minutes=max(1, minutes))
    rows = (
        await session.execute(
            select(AiMessage.user_id)
            .where(AiMessage.role == "chat")
            .where(AiMessage.user_id.is_not(None))
            .where(AiMessage.created_at >= since)
            .group_by(AiMessage.user_id)
        )
    ).scalars().all()
    ex = exclude or set()
    return [uid for uid in rows if uid not in ex]


async def _audience_active(
    session: AsyncSession, *, days: int, exclude: set[int] | None = None
) -> list[int]:
    """Игроки, активные за последние ``days`` дней (по last_active_at)."""
    from app.repositories import users as users_repo

    return await users_repo.get_active_user_ids(session, days, exclude=exclude)


async def resolve_audience(
    session: AsyncSession, *, scope: str, minutes: int = 60, days: int = 7,
) -> list[int]:
    """Возвращает список user_id целевой аудитории по описанию ``scope``.

    scope: ``recent`` (писали за ``minutes`` мин) / ``active`` (активны за
    ``days`` дней) / ``all`` (все игроки с балансом-историей).
    """
    if scope == "recent":
        ids = await _audience_recent_chat(session, minutes=minutes)
    elif scope == "all":
        ids = (await session.execute(select(User.user_id))).scalars().all()
        ids = list(ids)
    else:  # active
        ids = await _audience_active(session, days=days)
    return ids[:MAX_BULK_USERS]


# --- Инструмент: выдать ешки группе игроков ----------------------------------


async def grant_to_audience(
    session: AsyncSession, *, owner_id: int, user_ids: list[int], amount: int,
    note: str = "",
) -> ToolResult:
    """Начисляет каждому из ``user_ids`` по ``amount`` ешек (с предохранителями)."""
    if amount == 0:
        return ToolResult(ok=False, error="нулевая сумма")
    amount = max(-MAX_GRANT_PER_USER, min(MAX_GRANT_PER_USER, int(amount)))
    targets = list(dict.fromkeys(user_ids))[:MAX_BULK_USERS]
    if not targets:
        return ToolResult(ok=False, error="пустая аудитория")

    done = 0
    for uid in targets:
        try:
            await economy.change_balance(
                session, uid, amount, REASON_OWNER,
                {"action": "owner_grant", "note": note, "by": owner_id},
                allow_negative=False,
            )
            done += 1
        except Exception:  # noqa: BLE001
            logger.debug("grant to %s failed", uid, exc_info=True)
    await _audit(
        session, owner_id, "owner_grant_bulk", None,
        note or "массовая выдача",
        {"amount": amount, "targets": done, "note": note},
    )
    verb = "раздал" if amount > 0 else "снял"
    return ToolResult(
        ok=done > 0,
        summary=f"{verb} по {money(abs(amount))} — {done} игрокам",
        affected=done,
        meta={"amount": amount, "per_user": amount},
    )


# --- Инструмент: сбросить кулдаун действия группе ----------------------------


async def reset_cooldown_for(
    session: AsyncSession, *, owner_id: int, user_ids: list[int], action: str,
) -> ToolResult:
    """Сбрасывает кулдаун ``action`` (например ``farm``) у группы игроков."""
    from app.services import cooldowns as cd_service

    action = (action or "farm").strip().lower()
    targets = list(dict.fromkeys(user_ids))[:MAX_BULK_USERS]
    if not targets:
        return ToolResult(ok=False, error="пустая аудитория")
    done = 0
    for uid in targets:
        try:
            await cd_service.clear_cooldown(session, uid, action)
            done += 1
        except Exception:  # noqa: BLE001
            logger.debug("reset cd %s for %s failed", action, uid, exc_info=True)
    await _audit(
        session, owner_id, "owner_reset_cooldown", None,
        f"сброс кд {action}", {"action": action, "targets": done},
    )
    return ToolResult(
        ok=done > 0,
        summary=f"сбросил кулдаун «{action}» — {done} игрокам",
        affected=done,
        meta={"action": action},
    )


# --- Инструмент: розыгрыш ешек среди аудитории -------------------------------


async def giveaway(
    session: AsyncSession, *, owner_id: int, user_ids: list[int], pool: int,
    winners: int = 1, note: str = "",
) -> ToolResult:
    """Разыгрывает ``pool`` ешек между ``winners`` случайными из аудитории."""
    pool = max(1, min(MAX_GIVEAWAY_POOL, int(pool)))
    candidates = list(dict.fromkeys(user_ids))
    if not candidates:
        return ToolResult(ok=False, error="некого разыгрывать")
    winners = max(1, min(int(winners), len(candidates), 20))
    chosen = random.sample(candidates, winners)
    share = pool // winners
    if share <= 0:
        return ToolResult(ok=False, error="фонд меньше числа победителей")

    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, chosen)
    won: list[tuple[int, int]] = []
    for uid in chosen:
        try:
            await economy.change_balance(
                session, uid, share, REASON_OWNER,
                {"action": "owner_giveaway", "note": note, "by": owner_id},
            )
            won.append((uid, share))
        except Exception:  # noqa: BLE001
            logger.debug("giveaway payout to %s failed", uid, exc_info=True)
    await _audit(
        session, owner_id, "owner_giveaway", None, note or "розыгрыш",
        {"pool": pool, "winners": len(won), "share": share},
    )
    winners_str = ", ".join(
        f"{name_for(names, uid)} (+{money(amt)})" for uid, amt in won
    )
    return ToolResult(
        ok=bool(won),
        summary=f"розыгрыш: победител{'и' if len(won) > 1 else 'ь'} — {winners_str}",
        affected=len(won),
        meta={"pool": pool, "share": share, "winners": [u for u, _ in won]},
    )
