"""Mine normalized person/name mentions from raw chat archive."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiChatArchive, AiPersonMention

_MENTION_RE = re.compile(
    r"(?<![\w@])(@?[A-ZА-ЯЁ][\wА-Яа-яЁё-]{2,32}|[a-zA-Z]\w{2,24}\d\w*)",
    re.UNICODE,
)
_WORD_RE = re.compile(r"[\wА-Яа-яЁё-]+", re.UNICODE)

_STOP = {
    "это", "там", "тут", "вот", "если", "когда", "почему", "потому", "просто",
    "сегодня", "вчера", "завтра", "короче", "ладно", "блять", "бля", "нахуй",
    "telegram", "voice", "photo", "video", "sticker", "file", "гиф", "gif",
    "друн", "темный", "тёмный", "возня", "voznya",
}
_RU_SUFFIXES = (
    "ами", "ями", "ого", "ему", "ому", "ыми", "ими", "ой", "ей", "ою", "ею",
    "ом", "ем", "ах", "ях", "ов", "ев", "ия", "иям", "ию", "ия", "а", "я", "у", "ю", "е", "ы", "и",
)


@dataclass(frozen=True)
class MentionCandidate:
    mention: str
    mention_norm: str
    confidence: int = 50
    source_kind: str = "regex"


def normalize_mention(text: str) -> str:
    word = " ".join(_WORD_RE.findall((text or "").lower().replace("ё", "е")))
    word = word.lstrip("@")
    if " " in word:
        return word
    if word.endswith("ина") and len(word) > 5:
        return word[:-1]
    for suffix in _RU_SUFFIXES:
        if len(word) >= 5 and word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def extract_mentions(text: str, *, limit: int = 8) -> list[MentionCandidate]:
    out: list[MentionCandidate] = []
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(text or ""):
        raw = m.group(1).strip(".,:;!?()[]{}<>\"'«»")
        norm = normalize_mention(raw)
        if len(norm) < 3 or norm in _STOP or norm in seen:
            continue
        # All-caps/common sentence-leading words are noisy; keep @/mixed/digit
        # aliases and capitalized Cyrillic names.
        confidence = 70 if raw.startswith("@") else 55
        if any(ch.isdigit() for ch in raw):
            confidence = max(confidence, 65)
        seen.add(norm)
        out.append(MentionCandidate(raw, norm, confidence))
        if len(out) >= limit:
            break
    return out


def rows_from_archive(row: AiChatArchive) -> list[dict]:
    excerpt = " ".join((row.text or "").split())[:500]
    rows: list[dict] = []
    for cand in extract_mentions(row.text or ""):
        rows.append({
            "archive_id": int(row.id),
            "source": row.source,
            "source_message_id": int(row.source_message_id),
            "mention": cand.mention[:96],
            "mention_norm": cand.mention_norm[:96],
            "speaker_user_id": row.user_id,
            "speaker_name": (row.name or "")[:96],
            "text_excerpt": excerpt,
            "message_at": row.message_at,
            "confidence": cand.confidence,
            "source_kind": cand.source_kind,
            "meta": {},
        })
    return rows


async def mine_mentions_batch(
    session: AsyncSession,
    *,
    start_id: int = 0,
    limit: int = 1000,
) -> dict[str, int]:
    archive_rows = (
        await session.execute(
            select(AiChatArchive)
            .where(AiChatArchive.id > int(start_id))
            .order_by(AiChatArchive.id.asc())
            .limit(max(1, int(limit)))
        )
    ).scalars().all()
    rows: list[dict] = []
    max_id = int(start_id)
    for archive_row in archive_rows:
        max_id = max(max_id, int(archive_row.id))
        rows.extend(rows_from_archive(archive_row))
    inserted = 0
    if rows:
        stmt = insert(AiPersonMention.__table__).values(rows).on_conflict_do_nothing(
            index_elements=["archive_id", "mention_norm"]
        )
        result = await session.execute(stmt)
        inserted = int(result.rowcount or 0)
    return {
        "archive_seen": len(archive_rows),
        "mentions_seen": len(rows),
        "inserted": inserted,
        "max_id": max_id,
    }
