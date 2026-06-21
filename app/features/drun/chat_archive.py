"""Raw historical chat archive retrieval for Drun.

``ai_memories`` stores compressed facts. This module stores/searches real old
chat lines from Telegram export so Drun can recall concrete phrasing and scenes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.features.drun import embeddings as drun_embeddings
from app.features.drun.telegram_export_ingest import ExportMessage, SOURCE
from app.models import AiChatArchive

logger = get_logger(__name__)

_IMPORT_BATCH = 1000
_EMBED_BATCH = 64
_MIN_EMBED_CHARS = 15
_MAX_CONTEXT_LINES = 6


@dataclass(frozen=True)
class ArchiveHit:
    id: int
    user_id: int | None
    name: str
    text: str
    message_at: datetime | None
    score: float = 0.0


def archive_rows_from_export(
    messages: list[ExportMessage],
    *,
    source: str = SOURCE,
) -> list[dict]:
    """Pure conversion from parsed Telegram export messages to DB rows."""
    rows: list[dict] = []
    for msg in messages:
        clean = " ".join((msg.text or "").split())
        if not clean:
            continue
        rows.append({
            "source": source,
            "source_message_id": int(msg.message_id),
            "user_id": msg.user_id,
            "name": (msg.name or "")[:96],
            "text": clean[:2000],
            "message_at": msg.dt,
            "meta": {"reply_to_message_id": msg.reply_to_message_id}
            if msg.reply_to_message_id is not None else {},
        })
    return rows


async def import_export_messages(
    session: AsyncSession,
    messages: list[ExportMessage],
    *,
    source: str = SOURCE,
    batch_size: int = _IMPORT_BATCH,
) -> dict[str, int]:
    """Insert raw export messages into ``ai_chat_archive`` with idempotent dedupe."""
    rows = archive_rows_from_export(messages, source=source)
    stats = {"seen": len(rows), "inserted": 0}
    table = AiChatArchive.__table__
    for i in range(0, len(rows), max(1, batch_size)):
        batch = rows[i : i + batch_size]
        if not batch:
            continue
        stmt = insert(table).values(batch).on_conflict_do_nothing(
            index_elements=["source", "source_message_id"]
        )
        result = await session.execute(stmt)
        stats["inserted"] += int(result.rowcount or 0)
    return stats


async def backfill_archive_embeddings(
    session: AsyncSession,
    *,
    batch_size: int = _EMBED_BATCH,
) -> int:
    """Embed one batch of archive rows. Commit is done here like memory backfill."""
    cfg = await drun_embeddings.get_embedding_config(session)
    if not cfg.usable:
        return 0
    rows = (
        await session.execute(
            select(AiChatArchive.id, AiChatArchive.text)
            .where(AiChatArchive.embedding.is_(None))
            .where(func.length(AiChatArchive.text) >= _MIN_EMBED_CHARS)
            .order_by(AiChatArchive.id.asc())
            .limit(max(1, batch_size))
        )
    ).all()
    if not rows:
        return 0
    vecs = await drun_embeddings._embed_batch(cfg, "passage", [r.text for r in rows])
    if not vecs:
        return 0

    parts: list[str] = []
    params: dict[str, object] = {}
    for i, (row, vec) in enumerate(zip(rows, vecs, strict=True)):
        parts.append(f"(CAST(:id{i} AS bigint), CAST(:v{i} AS vector))")
        params[f"id{i}"] = int(row.id)
        params[f"v{i}"] = drun_embeddings._vector_literal(vec)
    await session.execute(
        text(
            "UPDATE ai_chat_archive AS a SET embedding = v.emb "
            "FROM (VALUES " + ", ".join(parts) + ") AS v(id, emb) "
            "WHERE a.id = v.id"
        ),
        params,
    )
    await session.commit()
    logger.info("chat archive embeddings backfill: %d rows", len(rows))
    return len(rows)


def setup_archive_embeddings_backfill(
    scheduler,
    sessionmaker,
    *,
    minutes: int = 10,
) -> None:
    """Register background archive embedding backfill.

    The archive can contain 100k+ rows, so this is intentionally slower than the
    normal memory backfill. Bulk import scripts can run larger batches manually.
    """

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                await backfill_archive_embeddings(session)
        except Exception:  # noqa: BLE001
            logger.warning("chat archive embeddings backfill job failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_chat_archive_embeddings_backfill",
        replace_existing=True,
    )


async def search_archive(
    session: AsyncSession,
    *,
    query: str,
    subject_id: int | None = None,
    limit: int = _MAX_CONTEXT_LINES,
) -> list[ArchiveHit]:
    """Hybrid search over raw archived lines: FTS + vector if available."""
    q = (query or "").strip()
    if not q:
        return []

    where = "WHERE TRUE"
    params: dict[str, object] = {"limit": int(limit), "query": q}
    if subject_id is not None:
        where += " AND user_id = :subject_id"
        params["subject_id"] = int(subject_id)

    tsq = "plainto_tsquery(CAST('russian' AS regconfig), CAST(:query AS text))"
    score_sql = f"ts_rank(text_tsv, {tsq}) * 6.0"
    filter_sql = f"text_tsv @@ {tsq}"

    query_vec = await drun_embeddings.embed_query(session, q)
    if query_vec is not None:
        params["qv"] = drun_embeddings._vector_literal(query_vec)
        vec_sim = "COALESCE(1 - (embedding <=> CAST(:qv AS vector)) / 2.0, 0)"
        score_sql += f" + ({vec_sim}) * 8.0"
        filter_sql = f"({filter_sql} OR (embedding IS NOT NULL AND {vec_sim} > 0.72))"

    sql = text(
        "SELECT id, user_id, name, text, message_at, "
        f"({score_sql}) AS score FROM ai_chat_archive {where} "
        f"AND ({filter_sql}) "
        "ORDER BY score DESC, message_at DESC NULLS LAST "
        "LIMIT :limit"
    )
    try:
        rows = (await session.execute(sql, params)).all()
    except Exception:  # noqa: BLE001
        logger.debug("chat archive hybrid search failed", exc_info=True)
        # Last-resort LIKE fallback for old/test schemas.
        stmt = select(AiChatArchive).where(AiChatArchive.text.ilike(f"%{q}%"))
        if subject_id is not None:
            stmt = stmt.where(AiChatArchive.user_id == subject_id)
        rows2 = (await session.execute(stmt.order_by(AiChatArchive.message_at.desc()).limit(limit))).scalars().all()
        return [ArchiveHit(
            id=int(r.id), user_id=r.user_id, name=r.name, text=r.text,
            message_at=r.message_at, score=0.0,
        ) for r in rows2]
    return [ArchiveHit(
        id=int(r.id), user_id=r.user_id, name=r.name or "?", text=r.text,
        message_at=r.message_at, score=float(r.score or 0.0),
    ) for r in rows]


def render_archive_hits(hits: list[ArchiveHit]) -> str:
    if not hits:
        return ""
    lines = [
        "# СЫРОЙ АРХИВ ЧАТА (реальные старые реплики; используй как фактуру, не выдумывай):",
    ]
    for hit in hits[:_MAX_CONTEXT_LINES]:
        when = hit.message_at.date().isoformat() if hit.message_at else "без даты"
        body = hit.text if len(hit.text) <= 220 else hit.text[:219].rstrip() + "…"
        lines.append(f"- [{when}] {hit.name}: {body}")
    return "\n".join(lines)


async def build_archive_block(
    session: AsyncSession,
    *,
    query: str | None,
    subject_id: int | None = None,
    limit: int = _MAX_CONTEXT_LINES,
) -> str:
    hits = await search_archive(
        session, query=query or "", subject_id=subject_id, limit=limit,
    )
    return render_archive_hits(hits)
