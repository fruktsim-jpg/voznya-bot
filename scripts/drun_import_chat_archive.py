#!/usr/bin/env python3
"""Import Telegram Desktop export into Drun raw chat archive.

This is separate from ``drun_ingest_telegram_export``: ingest distills facts into
``ai_memories``; archive keeps real old lines in ``ai_chat_archive`` for search.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.features.drun.chat_archive import (  # noqa: E402
    backfill_archive_embeddings,
    import_export_messages,
)
from app.features.drun.telegram_export_ingest import (  # noqa: E402
    filter_messages,
    load_export_messages,
)


async def _amain(args: argparse.Namespace) -> int:
    loaded_messages = load_export_messages(args.path)
    exclude_ids = {int(x) for x in args.exclude_user_id}
    messages = filter_messages(loaded_messages, exclude_user_ids=exclude_ids)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        stats = await import_export_messages(
            session,
            messages,
            batch_size=args.batch_size,
        )
        if args.apply:
            await session.commit()
        else:
            await session.rollback()

    embedded = 0
    if args.apply and args.embed_batches > 0:
        for _ in range(args.embed_batches):
            async with sessionmaker() as session:
                n = await backfill_archive_embeddings(
                    session,
                    batch_size=args.embed_batch_size,
                )
            embedded += n
            if n == 0:
                break

    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "loaded_messages": len(loaded_messages),
        "messages": len(messages),
        "excluded_user_ids": sorted(exclude_ids),
        "stats": stats,
        "embedded": embedded,
    }, ensure_ascii=False, indent=2))
    await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="path to Telegram Desktop result.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--embed-batches",
        type=int,
        default=0,
        help="run N archive embedding batches after import (apply only)",
    )
    parser.add_argument("--embed-batch-size", type=int, default=128)
    parser.add_argument(
        "--exclude-user-id",
        action="append",
        default=["8785112116"],
        help="Telegram user_id to exclude; repeatable. Defaults to Drun bot id.",
    )
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
