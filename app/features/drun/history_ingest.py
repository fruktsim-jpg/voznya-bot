"""Историческая память Друна из старых данных чата.

Это НЕ импорт короткой истории диалога и НЕ «Друн был там всегда». Старые Combot
снимки превращаются в аккуратные предложения для долгой памяти: кто старожил,
кто много писал, какие имена/алиасы известны, какую общую эпоху чата стоит
помнить. Запись в БД делает только явный apply-режим скрипта.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import AiMemory, CombotDailyStats, CombotUserStats, User

SOURCE = "historical_chat"
KIND_CHAT_ERA = "legend"
KIND_PLAYER_HISTORY = "trait"
KIND_PLAYER_ALIAS = "chat:nickname"


@dataclass(frozen=True)
class HistoricalMemoryProposal:
    """Одна кандидатная память из исторического чата."""

    subject_id: int | None
    kind: str
    fact: str
    weight: int
    source: str = SOURCE
    ttl_days: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "kind": self.kind,
            "fact": self.fact,
            "weight": self.weight,
            "source": self.source,
            "ttl_days": self.ttl_days,
        }


def _clean(text: object, limit: int = 160) -> str:
    return " ".join(str(text or "").split())[:limit]


def _display_name(row: CombotUserStats, live: User | None = None) -> str:
    if live is not None:
        display = live.display_name() if hasattr(live, "display_name") else ""
        for value in (display, getattr(live, "first_name", None), live.username):
            value = _clean(value, 80)
            if value:
                return value
    for value in (row.title, row.username, row.user_id):
        value = _clean(value, 80)
        if value:
            return value
    return f"id{row.user_id}"


def build_player_proposals(
    row: CombotUserStats,
    *,
    live_user: User | None = None,
    min_messages: int = 100,
) -> list[HistoricalMemoryProposal]:
    """Pure helper: Combot user snapshot -> memory proposals."""
    if int(row.messages or 0) < min_messages:
        return []
    name = _display_name(row, live_user)
    out: list[HistoricalMemoryProposal] = []
    messages = int(row.messages or 0)
    rep = int(row.rep or 0)
    xp = int(row.xp or 0)
    days = int(row.days_since_joined or 0)

    weight = 3 if messages >= 1000 else 2 if messages >= 300 else 1
    parts = [f"{name} — заметный участник старой истории чата"]
    parts.append(f"у него было около {messages} сообщений в Combot-снимке")
    if days > 0:
        parts.append(f"примерно {days} дней в чате на момент импорта")
    if rep:
        parts.append(f"репутация Combot: {rep}")
    if xp:
        parts.append(f"XP Combot: {xp}")
    out.append(HistoricalMemoryProposal(
        subject_id=int(row.user_id),
        kind=KIND_PLAYER_HISTORY,
        fact="; ".join(parts) + ".",
        weight=weight,
        ttl_days=None,
    ))

    title = _clean(row.title, 80)
    username = _clean(row.username, 80).lstrip("@")
    live_names = {
        _clean(getattr(live_user, "full_name", ""), 80).lower() if live_user else "",
        _clean(getattr(live_user, "username", ""), 80).lower().lstrip("@") if live_user else "",
    }
    for alias in (title, username):
        if not alias or alias.lower().lstrip("@") in live_names:
            continue
        out.append(HistoricalMemoryProposal(
            subject_id=int(row.user_id),
            kind=KIND_PLAYER_ALIAS,
            fact=f"Исторический Combot называл {name} как «{alias}».",
            weight=1,
            ttl_days=180,
        ))
    return out


async def build_world_proposals(session: AsyncSession) -> list[HistoricalMemoryProposal]:
    """Агрегаты Combot -> память про эпоху чата в целом."""
    row = (await session.execute(
        select(
            func.min(CombotDailyStats.day),
            func.max(CombotDailyStats.day),
            func.coalesce(func.sum(CombotDailyStats.messages), 0),
            func.max(CombotDailyStats.messages),
        )
    )).one()
    start, end, total, peak = row
    if start is None or end is None or int(total or 0) <= 0:
        return []
    return [HistoricalMemoryProposal(
        subject_id=None,
        kind=KIND_CHAT_ERA,
        fact=(
            f"Старая эпоха чата по Combot: с {start} по {end} накопилось "
            f"примерно {int(total)} сообщений; самый шумный день дал около "
            f"{int(peak or 0)} сообщений. Это фон старых легенд Возни."
        ),
        weight=2,
        ttl_days=None,
    )]


async def build_proposals(
    session: AsyncSession,
    *,
    limit: int = 50,
    min_messages: int = 100,
) -> list[HistoricalMemoryProposal]:
    """Собирает предложения памяти без записи в БД."""
    proposals = await build_world_proposals(session)
    rows = (await session.execute(
        select(CombotUserStats, User)
        .outerjoin(User, User.user_id == CombotUserStats.user_id)
        .where(CombotUserStats.messages >= min_messages)
        .order_by(CombotUserStats.messages.desc())
        .limit(limit)
    )).all()
    for combot_user, live_user in rows:
        proposals.extend(build_player_proposals(
            combot_user, live_user=live_user, min_messages=min_messages
        ))
    return proposals


async def apply_proposals(
    session: AsyncSession,
    proposals: list[HistoricalMemoryProposal],
    *,
    dry_run: bool = True,
) -> dict[str, int]:
    """Пишет новые исторические memories, пропуская точные дубли."""
    stats = {"seen": len(proposals), "inserted": 0, "skipped": 0}
    if not proposals:
        return stats
    existing = set((await session.execute(
        select(AiMemory.subject_id, AiMemory.kind, AiMemory.fact)
        .where(AiMemory.source == SOURCE)
    )).all())
    for p in proposals:
        key = (p.subject_id, p.kind, p.fact)
        if key in existing:
            stats["skipped"] += 1
            continue
        existing.add(key)
        if not dry_run:
            expires_at = now_utc() + timedelta(days=p.ttl_days) if p.ttl_days else None
            session.add(AiMemory(
                subject_id=p.subject_id,
                kind=p.kind,
                fact=p.fact,
                weight=max(1, min(3, int(p.weight))),
                source=p.source,
                expires_at=expires_at,
            ))
        stats["inserted"] += 1
    return stats
