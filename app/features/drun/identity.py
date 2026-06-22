"""Person identity resolver for Drun.

Raw memories/archives are not enough: questions like "кто такой Хинт" first
need a reliable name -> user_id/person candidate step. This module provides a
conservative resolver with confidence/evidence so prompts can be careful when a
name is ambiguous.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiChatArchive, AiMemory, AiPersonMention

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)

_QUERY_PREFIXES = (
    "кто такой", "кто такая", "кто это", "что знаешь про", "расскажи про",
    "что знаешь о", "что знаешь об", "расскажи о", "расскажи об",
    "память про", "память о", "досье на", "досье", "про", "найди человека", "человек найти",
    "person find", "find person",
)

_FILLER_WORDS = {
    "вообще", "там", "это", "этот", "эта", "тот", "та", "же", "бля", "блять",
    "нахуй", "пж", "плиз", "pls", "про", "о", "об", "чел", "человек",
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


@dataclass(frozen=True)
class DossierArchiveLine:
    name: str
    text: str
    message_at: datetime | None = None


@dataclass(frozen=True)
class DossierMemoryLine:
    kind: str
    fact: str
    weight: int = 0


@dataclass(frozen=True)
class DossierRelationshipLine:
    user_id: int | None
    name: str
    count: int
    direction: str


@dataclass(frozen=True)
class PersonDossier:
    candidate: PersonCandidate
    mention_lines: list[DossierArchiveLine] = field(default_factory=list)
    archive_lines: list[DossierArchiveLine] = field(default_factory=list)
    memories: list[DossierMemoryLine] = field(default_factory=list)
    relationships: list[DossierRelationshipLine] = field(default_factory=list)


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
    query_tokens = [w for w in _WORD_RE.findall(name.lower()) if w]

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

    # 1b) If the queried name appears in message text ("Карина", "карине"),
    # find the speakers who mention it most. This covers people who are talked
    # about a lot but rarely use that exact string as their display name.
    mention_rows = (
        await session.execute(
            select(
                AiChatArchive.user_id,
                AiChatArchive.name,
                func.count().label("cnt"),
            )
            .where(AiChatArchive.text.ilike(like))
            .where(AiChatArchive.user_id.is_not(None))
            .group_by(AiChatArchive.user_id, AiChatArchive.name)
            .order_by(func.count().desc())
            .limit(20)
        )
    ).all()
    for user_id, display_name, cnt in mention_rows:
        _merge_candidate(
            bucket,
            user_id=int(user_id) if user_id is not None else None,
            name=display_name or name,
            confidence=0.36,
            source="archive_text_mentioner",
            archive_hits=int(cnt or 0),
        )

    mention_index_rows = (
        await session.execute(
            select(
                AiPersonMention.speaker_user_id,
                AiPersonMention.speaker_name,
                func.count().label("cnt"),
            )
            .where(AiPersonMention.mention_norm == norm)
            .where(AiPersonMention.speaker_user_id.is_not(None))
            .group_by(AiPersonMention.speaker_user_id, AiPersonMention.speaker_name)
            .order_by(func.count().desc())
            .limit(20)
        )
    ).all()
    for user_id, speaker_name, cnt in mention_index_rows:
        _merge_candidate(
            bucket,
            user_id=int(user_id) if user_id is not None else None,
            name=speaker_name or name,
            confidence=0.48,
            source="person_mention_index",
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

    # 3) Very small Russian-name stemming fallback: Карина -> карин*, фрукта ->
    # фрукт*. This is intentionally weak evidence, but prevents "ничего не знаю"
    # for common inflected names in chat history.
    if not bucket and query_tokens:
        stem = query_tokens[0]
        if len(stem) > 4:
            stem = stem[:-1]
        stem_like = f"%{stem}%"
        rows = (
            await session.execute(
                select(AiChatArchive.user_id, AiChatArchive.name, func.count().label("cnt"))
                .where(AiChatArchive.text.ilike(stem_like))
                .where(AiChatArchive.user_id.is_not(None))
                .group_by(AiChatArchive.user_id, AiChatArchive.name)
                .order_by(func.count().desc())
                .limit(12)
            )
        ).all()
        for user_id, display_name, cnt in rows:
            _merge_candidate(
                bucket,
                user_id=int(user_id) if user_id is not None else None,
                name=display_name or name,
                confidence=0.28,
                source="archive_text_stem_mentioner",
                archive_hits=int(cnt or 0),
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


def render_dossier(dossier: PersonDossier) -> str:
    cand = dossier.candidate
    lines = ["# АВТО-ДОСЬЕ ЧЕЛОВЕКА"]
    uid = cand.user_id if cand.user_id is not None else "?"
    caution = "" if cand.confidence >= 0.78 else " Низкая уверенность: формулируй осторожно."
    aliases = ", ".join(cand.aliases[:8]) or cand.name
    lines.append(
        f"- identity: user_id={uid}; name={cand.name}; conf={cand.confidence:.2f}; "
        f"aliases=[{aliases}].{caution}"
    )
    if dossier.memories:
        lines.append("## Сжатые факты/черты")
        for mem in dossier.memories[:8]:
            tag = f"[{mem.kind} w={mem.weight}]"
            lines.append(f"- {tag} {mem.fact}")
    if dossier.relationships:
        lines.append("## Авто-связи по reply-графу")
        for rel in dossier.relationships[:6]:
            uid = rel.user_id if rel.user_id is not None else "?"
            lines.append(
                f"- {rel.name} (user_id={uid}): {rel.direction}, reply-связей={rel.count}"
            )
    if dossier.mention_lines:
        lines.append("## Реальные упоминания имени в чате")
        for line in dossier.mention_lines[:6]:
            when = line.message_at.date().isoformat() if line.message_at else "без даты"
            text = line.text if len(line.text) <= 180 else line.text[:179].rstrip() + "…"
            lines.append(f"- [{when}] {line.name}: {text}")
    if dossier.archive_lines:
        lines.append("## Реальные старые реплики этого человека")
        for line in dossier.archive_lines[:6]:
            when = line.message_at.date().isoformat() if line.message_at else "без даты"
            text = line.text if len(line.text) <= 180 else line.text[:179].rstrip() + "…"
            lines.append(f"- [{when}] {line.name}: {text}")
    lines.append(
        "## Правило ответа\n"
        "- Если confidence низкий или фактов мало, не делай вид, что уверен; скажи 'если ты про этого...'."
    )
    return "\n".join(lines)


async def build_relationship_lines(
    session: AsyncSession,
    user_id: int,
    *,
    limit: int = 6,
) -> list[DossierRelationshipLine]:
    """Infer social edges from Telegram reply links in raw archive."""
    sql = text(
        """
        WITH edges AS (
            SELECT
                replied.user_id AS other_user_id,
                replied.name AS other_name,
                'он отвечал им' AS direction,
                count(*) AS cnt
            FROM ai_chat_archive AS msg
            JOIN ai_chat_archive AS replied
              ON replied.source = msg.source
             AND replied.source_message_id = CAST(msg.meta->>'reply_to_message_id' AS bigint)
            WHERE msg.user_id = :uid
              AND msg.meta ? 'reply_to_message_id'
              AND replied.user_id IS NOT NULL
              AND replied.user_id <> :uid
            GROUP BY replied.user_id, replied.name
            UNION ALL
            SELECT
                msg.user_id AS other_user_id,
                msg.name AS other_name,
                'ему отвечали' AS direction,
                count(*) AS cnt
            FROM ai_chat_archive AS replied
            JOIN ai_chat_archive AS msg
              ON replied.source = msg.source
             AND replied.source_message_id = CAST(msg.meta->>'reply_to_message_id' AS bigint)
            WHERE replied.user_id = :uid
              AND msg.meta ? 'reply_to_message_id'
              AND msg.user_id IS NOT NULL
              AND msg.user_id <> :uid
            GROUP BY msg.user_id, msg.name
        )
        SELECT other_user_id, other_name, direction, sum(cnt) AS cnt
        FROM edges
        GROUP BY other_user_id, other_name, direction
        ORDER BY sum(cnt) DESC
        LIMIT :limit
        """
    )
    try:
        rows = (await session.execute(sql, {"uid": int(user_id), "limit": int(limit)})).all()
    except Exception:  # noqa: BLE001
        return []
    return [
        DossierRelationshipLine(
            user_id=int(r.other_user_id) if r.other_user_id is not None else None,
            name=r.other_name or "?",
            count=int(r.cnt or 0),
            direction=r.direction or "reply-связь",
        )
        for r in rows
    ]


async def build_person_dossier(
    session: AsyncSession,
    query: str,
    *,
    limit_archive: int = 6,
    limit_memories: int = 8,
) -> PersonDossier | None:
    candidates = await resolve_person(session, query, limit=1)
    if not candidates:
        return None
    cand = candidates[0]

    archive_lines: list[DossierArchiveLine] = []
    if cand.user_id is not None:
        rows = (
            await session.execute(
                select(AiChatArchive.name, AiChatArchive.text, AiChatArchive.message_at)
                .where(AiChatArchive.user_id == cand.user_id)
                .where(func.length(AiChatArchive.text) >= 8)
                .order_by(AiChatArchive.message_at.desc().nullslast())
                .limit(limit_archive)
            )
        ).all()
        archive_lines = [
            DossierArchiveLine(name=name or cand.name, text=text or "", message_at=message_at)
            for name, text, message_at in rows
        ]

    mention_lines: list[DossierArchiveLine] = []
    person_name = extract_person_query(query)
    if person_name:
        mention_like = f"%{person_name}%"
        rows = (
            await session.execute(
                select(AiChatArchive.name, AiChatArchive.text, AiChatArchive.message_at)
                .where(AiChatArchive.text.ilike(mention_like))
                .order_by(AiChatArchive.message_at.desc().nullslast())
                .limit(6)
            )
        ).all()
        mention_lines = [
            DossierArchiveLine(name=name or "?", text=text or "", message_at=message_at)
            for name, text, message_at in rows
            if text
        ]

    memories: list[DossierMemoryLine] = []
    if cand.user_id is not None:
        rows = (
            await session.execute(
                select(AiMemory.kind, AiMemory.fact, AiMemory.weight)
                .where(AiMemory.subject_id == cand.user_id)
                .order_by(AiMemory.weight.desc(), AiMemory.created_at.desc())
                .limit(limit_memories)
            )
        ).all()
        memories = [
            DossierMemoryLine(kind=kind or "fact", fact=fact or "", weight=int(weight or 0))
            for kind, fact, weight in rows
            if fact
        ]

    relationships: list[DossierRelationshipLine] = []
    if cand.user_id is not None:
        relationships = await build_relationship_lines(session, int(cand.user_id))

    return PersonDossier(
        candidate=cand,
        mention_lines=mention_lines,
        archive_lines=archive_lines,
        memories=memories,
        relationships=relationships,
    )


async def build_identity_block(session: AsyncSession, query: str | None) -> str:
    q = extract_person_query(query or "")
    if not q:
        return ""
    candidates = await resolve_person(session, q, limit=5)
    parts = [render_candidates(candidates, title="# КТО ЭТО МОЖЕТ БЫТЬ")]
    if candidates and candidates[0].confidence >= 0.45:
        dossier = await build_person_dossier(session, q)
        if dossier is not None:
            parts.append(render_dossier(dossier))
    return "\n\n".join(part for part in parts if part)
