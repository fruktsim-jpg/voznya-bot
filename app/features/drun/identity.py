"""Person identity resolver for Drun.

Raw memories/archives are not enough: questions like "кто такой Хинт" first
need a reliable name -> user_id/person candidate step. This module provides a
conservative resolver with confidence/evidence so prompts can be careful when a
name is ambiguous.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiChatArchive, AiMemory

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)

_QUERY_PREFIXES = (
    "кто такой", "кто такая", "кто это", "что знаешь про", "расскажи про",
    "память про", "досье на", "досье", "про", "найди человека", "человек найти",
    "person find", "find person",
)

_FILLER_WORDS = {
    "вообще", "там", "это", "этот", "эта", "тот", "та", "же", "бля", "блять",
    "нахуй", "пж", "плиз", "pls", "про", "чел", "человек",
}


@dataclass
class PersonCandidate:
    user_id: int | None
    name: str
    confidence: float
    sources: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    memory_hits: int = 0
    archive_hits: int = 0


def normalize_name(text: str) -> str:
    words = _WORD_RE.findall((text or "").lower())
    return " ".join(words)


def extract_person_query(text: str) -> str:
    """Extract likely person name from a natural-language owner/chat query."""
    body = (text or "").strip()
    low = body.lower()
    for prefix in _QUERY_PREFIXES:
        if low.startswith(prefix):
            body = body[len(prefix):].strip(" :,-—")
            break
    # Keep the first compact name-like span; extra words are usually the actual
    # question and hurt exact archive speaker matching.
    words = [w for w in _WORD_RE.findall(body) if w.lower() not in _FILLER_WORDS]
    return " ".join(words[:3]).strip()


async def _attach_archive_aliases(
    session: AsyncSession,
    bucket: dict[int | str, PersonCandidate],
) -> None:
    """For matched user_ids, add their other display names from archive.

    This is the automatic identity-learning path: if one name resolves to a
    concrete speaker, the resolver learns that user's other historic names
    without owner hand-labeling.
    """
    user_ids = [key for key in bucket if isinstance(key, int)]
    if not user_ids:
        return
    rows = (
        await session.execute(
            select(
                AiChatArchive.user_id,
                AiChatArchive.name,
                func.count().label("cnt"),
            )
            .where(AiChatArchive.user_id.in_(user_ids))
            .where(AiChatArchive.name != "")
            .group_by(AiChatArchive.user_id, AiChatArchive.name)
            .order_by(func.count().desc())
            .limit(max(20, len(user_ids) * 8))
        )
    ).all()
    for user_id, display_name, cnt in rows:
        if user_id is None or not display_name:
            continue
        cand = bucket.get(int(user_id))
        if cand is None:
            continue
        if display_name not in cand.aliases:
            cand.aliases.append(display_name)
        if "archive_same_user_alias" not in cand.sources:
            cand.sources.append("archive_same_user_alias")
        cand.archive_hits += int(cnt or 0)


def _merge_candidate(
    bucket: dict[int | str, PersonCandidate],
    *,
    user_id: int | None,
    name: str,
    confidence: float,
    source: str,
    archive_hits: int = 0,
    memory_hits: int = 0,
) -> None:
    key: int | str = user_id if user_id is not None else f"name:{normalize_name(name)}"
    if key not in bucket:
        bucket[key] = PersonCandidate(
            user_id=user_id,
            name=name,
            confidence=confidence,
            sources=[source],
            aliases=[name] if name else [],
            archive_hits=archive_hits,
            memory_hits=memory_hits,
        )
        return
    cand = bucket[key]
    cand.confidence = max(cand.confidence, confidence)
    if source not in cand.sources:
        cand.sources.append(source)
    if name and name not in cand.aliases:
        cand.aliases.append(name)
    cand.archive_hits += archive_hits
    cand.memory_hits += memory_hits
    if len(name) > len(cand.name):
        cand.name = name


def rank_candidates(candidates: list[PersonCandidate]) -> list[PersonCandidate]:
    for cand in candidates:
        evidence_bonus = min(0.18, cand.archive_hits / 500.0 + cand.memory_hits / 50.0)
        source_bonus = min(0.08, len(cand.sources) * 0.02)
        cand.confidence = min(0.99, cand.confidence + evidence_bonus + source_bonus)
    return sorted(
        candidates,
        key=lambda c: (c.confidence, c.archive_hits, c.memory_hits, c.user_id or 0),
        reverse=True,
    )


async def resolve_person(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 5,
) -> list[PersonCandidate]:
    name = extract_person_query(query)
    norm = normalize_name(name)
    if not norm:
        return []

    bucket: dict[int | str, PersonCandidate] = {}
    like = f"%{name}%"

    # 1) Speaker names from raw archive. This is the most concrete identity
    # evidence: user_id + actual Telegram display names/frequency.
    archive_rows = (
        await session.execute(
            select(
                AiChatArchive.user_id,
                AiChatArchive.name,
                func.count().label("cnt"),
            )
            .where(AiChatArchive.name.ilike(like))
            .group_by(AiChatArchive.user_id, AiChatArchive.name)
            .order_by(func.count().desc())
            .limit(20)
        )
    ).all()
    for user_id, display_name, cnt in archive_rows:
        exact = normalize_name(display_name) == norm
        _merge_candidate(
            bucket,
            user_id=user_id,
            name=display_name or name,
            confidence=0.86 if exact else 0.62,
            source="archive_name_exact" if exact else "archive_name_fuzzy",
            archive_hits=int(cnt or 0),
        )

    # 2) Long-memory subjects mentioning the name. Lower confidence than speaker
    # identity, but useful for aliases/kлички that don't appear as display names.
    memory_rows = (
        await session.execute(
            select(AiMemory.subject_id, func.count().label("cnt"))
            .where(AiMemory.subject_id.is_not(None))
            .where(or_(AiMemory.fact.ilike(like), AiMemory.kind.ilike(like)))
            .group_by(AiMemory.subject_id)
            .order_by(func.count().desc())
            .limit(20)
        )
    ).all()
    for subject_id, cnt in memory_rows:
        _merge_candidate(
            bucket,
            user_id=int(subject_id) if subject_id is not None else None,
            name=name,
            confidence=0.55,
            source="memory_fact",
            memory_hits=int(cnt or 0),
        )

    await _attach_archive_aliases(session, bucket)
    return rank_candidates(list(bucket.values()))[:limit]


def render_candidates(candidates: list[PersonCandidate], *, title: str = "# IDENTITY RESOLVER") -> str:
    if not candidates:
        return f"{title}\n- кандидатов не найдено; отвечай осторожно, не выдумывай личность."
    lines = [title]
    for cand in candidates:
        uid = cand.user_id if cand.user_id is not None else "?"
        aliases = ", ".join(cand.aliases[:5]) or cand.name
        sources = ", ".join(cand.sources)
        caution = "" if cand.confidence >= 0.78 else " (низкая уверенность)"
        lines.append(
            f"- user_id={uid}; name={cand.name}; conf={cand.confidence:.2f}{caution}; "
            f"aliases=[{aliases}]; archive={cand.archive_hits}; memory={cand.memory_hits}; sources={sources}"
        )
    return "\n".join(lines)


async def build_identity_block(session: AsyncSession, query: str | None) -> str:
    q = extract_person_query(query or "")
    if not q:
        return ""
    candidates = await resolve_person(session, q, limit=5)
    return render_candidates(candidates, title="# КТО ЭТО МОЖЕТ БЫТЬ")
