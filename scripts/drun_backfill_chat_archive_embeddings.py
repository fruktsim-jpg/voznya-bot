#!/usr/bin/env python3
"""Backfill embeddings for Drun raw chat archive.

The archive import can insert 100k+ rows quickly, while embeddings are CPU-bound.
This script runs a bounded number of batches without re-reading result.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.features.drun.chat_archive import backfill_archive_embeddings  # noqa: E402
from app.models import AiChatArchive  # noqa: E402


async def _counts(session) -> dict[str, int]:
    total = int(await session.scalar(select(func.count()).select_from(AiChatArchive)) or 0)
    embedded = int(
        await session.scalar(
            select(func.count()).select_from(AiChatArchive).where(
                AiChatArchive.embedding.is_not(None)
            )
        ) or 0
    )
    return {"total": total, "embedded": embedded, "missing": max(0, total - embedded)}


async def _amain(args: argparse.Namespace) -> int:
    sessionmaker = get_sessionmaker()
    before: dict[str, int]
    async with sessionmaker() as session:
        before = await _counts(session)

    processed = 0
    batches = 0
    if args.apply:
        for _ in range(max(1, args.max_batches)):
            async with sessionmaker() as session:
                n = await backfill_archive_embeddings(
                    session,
                    batch_size=args.batch_size,
                )
            if n <= 0:
                break
            processed += n
            batches += 1

    async with sessionmaker() as session:
        after = await _counts(session)

    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "batch_size": args.batch_size,
        "max_batches": args.max_batches,
        "batches": batches,
        "processed": processed,
        "before": before,
        "after": after,
    }, ensure_ascii=False, indent=2))
    await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--max-batches", type=int, default=10)
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
