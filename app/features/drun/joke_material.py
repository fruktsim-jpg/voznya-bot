"""Joke-specific material pack for Drun.

General memory recall is optimized for answering/banter, not for comedy. When a
user asks for a joke, Drun needs diverse premises: one local lore bit, one person
bit if a name is present, and one archive/memory hook. This module builds that
compact pack and explicitly excludes stale economy/duel fallback unless asked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.drun import identity as drun_identity
from app.models import AiChatArchive, AiMemory

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)
_ECONOMY_DUEL_WORDS = (
    "ешк", "казино", "баланс", "дуэл", "кд", "kd", "ставк", "проиграл",
    "проигран", "банк", "монет",
)
_JOKE_PREFIXES = (
    "расскажи анекдот", "дай анекдот", "придумай анекдот", "расскажи шутку",
    "дай шутку", "придумай шутку", "пошути про", "пошути о", "пошути об",
    "пошути", "зарофли", "рассмеши",
)


@dataclass(frozen=True)
class JokeMaterial:
    kind: str
    text: str
    source: str = ""


def _clean_query(text: str) -> str:
    body = (text or "").strip()
    low = body.lower()
    for prefix in _JOKE_PREFIXES:
        if low.startswith(prefix):
            return body[len(prefix):].strip(" :,-—")
    return body


def _bad_stale_material(text: str, *, query: str) -> bool:
    low = (text or "").lower()
    q = (query or "").lower()
    if any(w in q for w in _ECONOMY_DUEL_WORDS if w not in {"проиграл", "проигран"}):
        return False
    return any(w in low for w in _ECONOMY_DUEL_WORDS)


def _line(text: str, limit: int = 180) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


async def build_joke_materials(
    session: AsyncSession,
    *,
    query: str,
    limit: int = 7,
) -> list[JokeMaterial]:
    """Collect diverse, non-stale premises for a requested joke."""
    topic = _clean_query(query)
    materials: list[JokeMaterial] = []
    seen: set[str] = set()

    def add(kind: str, text: str, source: str = "") -> None:
        clean = _line(text)
        key = clean.lower()
        if not clean or key in seen or _bad_stale_material(clean, query=query):
            return
        seen.add(key)
        materials.append(JokeMaterial(kind=kind, text=clean, source=source))

    # If query names a person, use the identity/dossier pipeline as premise.
    candidates = await drun_identity.resolve_person(session, topic or query, limit=1)
    if candidates:
        cand = candidates[0]
        aliases = ", ".join(cand.aliases[:4]) or cand.name
        add(
            "person",
            f"возможный герой шутки: {cand.name}, aliases=[{aliases}], confidence={cand.confidence:.2f}",
            "identity",
        )
        if cand.user_id is not None:
            mem_rows = (
                await session.execute(
                    select(AiMemory.kind, AiMemory.fact, AiMemory.weight)
                    .where(AiMemory.subject_id == cand.user_id)
                    .order_by(AiMemory.weight.desc(), AiMemory.created_at.desc())
                    .limit(6)
                )
            ).all()
            for kind, fact, weight in mem_rows:
                add("person_fact", f"[{kind} w={weight}] {fact}", "ai_memories")

    # Topic/mention archive lines are better joke premises than last 3 messages.
    if topic:
        like = f"%{topic}%"
        rows = (
            await session.execute(
                select(AiChatArchive.name, AiChatArchive.text, AiChatArchive.message_at)
                .where(AiChatArchive.text.ilike(like))
                .where(func.length(AiChatArchive.text) >= 10)
                .order_by(AiChatArchive.message_at.desc().nullslast())
                .limit(8)
            )
        ).all()
        for name, text, _message_at in rows:
            add("archive_mention", f"{name or '?'}: {text}", "ai_chat_archive")

    # General non-person lore/facts give absurd VOZNYA material.
    lore_rows = (
        await session.execute(
            select(AiMemory.kind, AiMemory.fact, AiMemory.weight)
            .where(AiMemory.subject_id.is_(None))
            .where(AiMemory.kind.in_(["legend", "storyline", "prediction", "lesson", "chat:meme"]))
            .order_by(AiMemory.weight.desc(), AiMemory.created_at.desc())
            .limit(10)
        )
    ).all()
    for kind, fact, weight in lore_rows:
        add("world_lore", f"[{kind} w={weight}] {fact}", "ai_memories")

    # If no topic-specific rows exist, fallback to recent quirky long enough chat
    # lines, still filtered away from economy/duel spam.
    if len(materials) < 3:
        rows = (
            await session.execute(
                select(AiChatArchive.name, AiChatArchive.text, AiChatArchive.message_at)
                .where(func.length(AiChatArchive.text) >= 25)
                .order_by(AiChatArchive.message_at.desc().nullslast())
                .limit(12)
            )
        ).all()
        for name, text, _message_at in rows:
            add("recent_archive", f"{name or '?'}: {text}", "ai_chat_archive")

    return materials[:limit]


def render_joke_materials(materials: list[JokeMaterial]) -> str:
    if not materials:
        return ""
    lines = [
        "# МАТЕРИАЛ ДЛЯ ШУТКИ (выбери 1-2 зацепки, не пересказывай всё):",
        "# Запрещено по умолчанию: ешки/казино/дуэли/КД, если пользователь сам не просил.",
    ]
    for item in materials:
        src = f"/{item.source}" if item.source else ""
        lines.append(f"- [{item.kind}{src}] {item.text}")
    return "\n".join(lines)


async def build_joke_material_block(session: AsyncSession, *, query: str) -> str:
    return render_joke_materials(await build_joke_materials(session, query=query))
