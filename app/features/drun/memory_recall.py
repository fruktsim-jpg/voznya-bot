"""Structured long-memory recall for Drun.

The old context path rendered a flat top-N list from ``ai_memories``. That made
the model overuse a few high-weight facts and troll the same person by the same
three hooks. This module turns retrieval into a small dossier: stable identity,
fresh hooks, relationships, episodes, and world lore, with caps per kind and a
penalty for facts Drun has just used in recent posts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.drun import memory as drun_memory

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)

# Pull a wide candidate pool, then diversify in Python. This is cheap: the SQL
# ranker already uses indexes and the prompt still receives a compact subset.
_CANDIDATE_LIMIT = 80
_RECENT_POSTS_LIMIT = 10
_TOTAL_CAP = 24

_SECTION_ORDER = (
    "identity",
    "fresh",
    "relationships",
    "episodes",
    "world",
    "other",
)

_SECTION_TITLE = {
    "identity": "Кто это / устойчивое досье",
    "fresh": "Свежие и тематические зацепки",
    "relationships": "Связи, конфликты, отношения",
    "episodes": "Конкретные эпизоды из истории",
    "world": "Лор и мемы мира",
    "other": "Прочее полезное",
}

_SECTION_CAP = {
    "identity": 6,
    "fresh": 6,
    "relationships": 5,
    "episodes": 5,
    "world": 5,
    "other": 4,
}

_KIND_CAP = {
    "chat:nickname": 2,
    "chat:meme": 3,
    "chat:joke": 2,
    "chat:trait": 4,
    "trait": 4,
    "opinion": 2,
    "rivalry": 3,
    "chat:relationship": 3,
    "legend": 3,
    "storyline": 2,
    "prediction": 2,
}

_TAG = {
    "chat:nickname": "[кличка] ",
    "chat:joke": "[шутка] ",
    "chat:meme": "[мем] ",
    "rivalry": "[конфликт] ",
    "chat:relationship": "[связь] ",
    "opinion": "[мнение] ",
    "legend": "[легенда] ",
    "storyline": "[сюжет] ",
    "prediction": "[прогноз] ",
}


class MemoryLike(Protocol):
    id: int
    subject_id: int | None
    kind: str
    fact: str
    weight: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class RecallItem:
    memory: MemoryLike
    section: str
    score: float
    repeated: bool = False


def _signature(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if len(w) >= 3}


def _section_for(mem: MemoryLike) -> str:
    kind = (getattr(mem, "kind", "") or "").lower()
    if kind.startswith("episode:"):
        return "episodes"
    if kind in {"chat:relationship", "rivalry"}:
        return "relationships"
    if kind in {"chat:nickname", "chat:trait", "trait", "opinion", "fact"}:
        return "identity"
    if kind in {"chat:meme", "chat:joke", "chat:topic"}:
        return "fresh"
    if kind in {"legend", "storyline", "prediction", "lesson"}:
        return "world"
    if getattr(mem, "subject_id", None) is None:
        return "world"
    return "other"


def _age_bonus(mem: MemoryLike, now: datetime) -> float:
    created = getattr(mem, "created_at", None)
    if created is None:
        return 0.0
    try:
        if created.tzinfo is None:
            age_days = max(0.0, (now.replace(tzinfo=None) - created).total_seconds() / 86400.0)
        else:
            age_days = max(0.0, (now - created).total_seconds() / 86400.0)
    except Exception:  # noqa: BLE001
        return 0.0
    return 2.0 * (0.5 ** (age_days / 21.0))


def _recently_used(mem: MemoryLike, recent_posts: Iterable[str]) -> bool:
    sig = _signature(getattr(mem, "fact", ""))
    if len(sig) < 12:
        return False
    fact_tokens = set(sig.split())
    if len(fact_tokens) < 3:
        return any(sig and sig in _signature(post) for post in recent_posts)
    for post in recent_posts:
        post_sig = _signature(post)
        if sig and sig in post_sig:
            return True
        post_tokens = set(post_sig.split())
        if len(fact_tokens & post_tokens) >= max(3, int(len(fact_tokens) * 0.75)):
            return True
    return False


def _score(mem: MemoryLike, *, query: str | None, recent_posts: Iterable[str], now: datetime) -> tuple[float, bool]:
    score = float(getattr(mem, "weight", 0) or 0) + _age_bonus(mem, now)
    q_tokens = _tokens(query or "")
    if q_tokens:
        score += 1.5 * len(q_tokens & _tokens(getattr(mem, "fact", "")))
    repeated = _recently_used(mem, recent_posts)
    if repeated:
        score -= 5.0
    return score, repeated


def select_recall_items(
    memories: Iterable[MemoryLike],
    *,
    query: str | None = None,
    recent_posts: Iterable[str] = (),
    now: datetime | None = None,
    total_cap: int = _TOTAL_CAP,
) -> list[RecallItem]:
    """Pure selection layer: dedupe, score, cap per kind/section, diversify."""
    current = now or datetime.now(timezone.utc)
    posts = list(recent_posts)
    seen_signatures: set[tuple[int | None, str]] = set()
    candidates: list[RecallItem] = []
    for mem in memories:
        fact = (getattr(mem, "fact", "") or "").strip()
        sig = _signature(fact)
        if not fact or not sig:
            continue
        dedupe_key = (getattr(mem, "subject_id", None), sig)
        if dedupe_key in seen_signatures:
            continue
        seen_signatures.add(dedupe_key)
        score, repeated = _score(mem, query=query, recent_posts=posts, now=current)
        candidates.append(RecallItem(
            memory=mem,
            section=_section_for(mem),
            score=score,
            repeated=repeated,
        ))

    candidates.sort(key=lambda item: (item.repeated, -item.score, -int(getattr(item.memory, "id", 0))))

    selected: list[RecallItem] = []
    section_counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    for item in candidates:
        if len(selected) >= total_cap:
            break
        section = item.section
        kind = (getattr(item.memory, "kind", "") or "").lower()
        if section_counts.get(section, 0) >= _SECTION_CAP.get(section, 4):
            continue
        if kind_counts.get(kind, 0) >= _KIND_CAP.get(kind, 4):
            continue
        selected.append(item)
        section_counts[section] = section_counts.get(section, 0) + 1
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    selected.sort(key=lambda item: (_SECTION_ORDER.index(item.section), -item.score))
    return selected


def render_recall(items: Iterable[RecallItem]) -> str:
    grouped: dict[str, list[RecallItem]] = {section: [] for section in _SECTION_ORDER}
    repeated: list[str] = []
    for item in items:
        grouped.setdefault(item.section, []).append(item)
        if item.repeated:
            repeated.append(getattr(item.memory, "fact", ""))

    if not any(grouped.values()):
        return ""

    lines = [
        "# ДОЛГАЯ ПАМЯТЬ ДРУНА (выбирай 1-3 детали, не долби всё подряд):",
        "# Не зацикливайся на одном факте: меняй угол, вспоминай эпизоды и связи.",
    ]
    for section in _SECTION_ORDER:
        section_items = grouped.get(section) or []
        if not section_items:
            continue
        lines.append(f"## {_SECTION_TITLE[section]}")
        for item in section_items:
            mem = item.memory
            kind = (getattr(mem, "kind", "") or "").lower()
            prefix = _TAG.get(kind, "")
            lines.append(f"- {prefix}{mem.fact}")

    if repeated:
        lines.append("## Уже заезжено недавно")
        lines.append("- Эти факты НЕ повторяй тем же заходом; если используешь, меняй угол:")
        for fact in repeated[:4]:
            lines.append(f"  {fact[:140]}")

    return "\n".join(lines)


async def build_recall_block(
    session: AsyncSession,
    *,
    subject_id: int | None,
    query: str | None,
    channel: str = "chat",
) -> str:
    memories = await drun_memory.scored_memories(
        session,
        subject_id=subject_id,
        query=query,
        limit=_CANDIDATE_LIMIT,
    )
    if not memories:
        return ""
    recent_posts = await drun_memory.recent_self_posts(
        session,
        channel=channel,
        limit=_RECENT_POSTS_LIMIT,
    )
    items = select_recall_items(memories, query=query, recent_posts=recent_posts)
    return render_recall(items)
