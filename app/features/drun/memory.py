"""Память друна: краткосрочная (история) и долгосрочная (факты).

* Краткосрочная — таблица ``ai_messages``: последние реплики диалога/постов в
  канале. Используется как контекст «о чём недавно говорили» и анти-повтор.
* Долгосрочная — таблица ``ai_memories``: устойчивые факты об игроках/мире
  («X — самый богатый», «Y слил 500к»). Подмешиваются в контекст по весу.

Память пишет только друн/бот. Никаких FK (соглашение проекта).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import AiMemory, AiMessage

# --- Краткосрочная память (история) -----------------------------------------


async def add_message(
    session: AsyncSession,
    *,
    role: str,
    content: str,
    channel: str = "chat",
    user_id: int | None = None,
    trigger_event_id: int | None = None,
    tokens: int | None = None,
    meta: dict[str, Any] | None = None,
) -> AiMessage:
    """Записывает реплику в историю. Commit — на вызывающем."""
    msg = AiMessage(
        role=role,
        content=content,
        channel=channel,
        user_id=user_id,
        trigger_event_id=trigger_event_id,
        tokens=tokens,
        meta=meta or {},
    )
    session.add(msg)
    await session.flush()
    return msg


async def recent_messages(
    session: AsyncSession, *, channel: str = "chat", limit: int = 10
) -> list[AiMessage]:
    """Возвращает последние реплики канала в хронологическом порядке."""
    rows = (
        await session.execute(
            select(AiMessage)
            .where(AiMessage.channel == channel)
            .order_by(AiMessage.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(reversed(rows))


# --- Долгосрочная память (факты) --------------------------------------------


async def remember(
    session: AsyncSession,
    *,
    fact: str,
    subject_id: int | None = None,
    kind: str = "fact",
    weight: int = 1,
    source: str | None = "auto",
    expires_at: datetime | None = None,
) -> AiMemory:
    """Сохраняет факт в долгосрочную память. Commit — на вызывающем."""
    mem = AiMemory(
        subject_id=subject_id,
        kind=kind,
        fact=fact,
        weight=weight,
        source=source,
        expires_at=expires_at,
    )
    session.add(mem)
    await session.flush()
    return mem


async def relevant_memories(
    session: AsyncSession,
    *,
    subject_id: int | None = None,
    limit: int = 8,
) -> list[AiMemory]:
    """Факты для контекста: про мир + (опционально) про конкретного игрока.

    Отсекает протухшие (``expires_at`` в прошлом), сортирует по весу.
    """
    now = now_utc()
    not_expired = or_(AiMemory.expires_at.is_(None), AiMemory.expires_at > now)
    if subject_id is not None:
        scope = or_(AiMemory.subject_id.is_(None), AiMemory.subject_id == subject_id)
    else:
        scope = AiMemory.subject_id.is_(None)
    rows = (
        await session.execute(
            select(AiMemory)
            .where(and_(not_expired, scope))
            .order_by(AiMemory.weight.desc(), AiMemory.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)
