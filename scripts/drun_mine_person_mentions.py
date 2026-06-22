#!/usr/bin/env python3
"""Mine ai_person_mentions from ai_chat_archive."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.features.drun.person_mentions import mine_mentions_batch  # noqa: E402


async def _amain(args: argparse.Namespace) -> int:
    sessionmaker = get_sessionmaker()
    stats_total = {"archive_seen": 0, "mentions_seen": 0, "inserted": 0, "max_id": args.start_id}
    current = args.start_id
    async with sessionmaker() as session:
        for _ in range(max(1, args.max_batches)):
            stats = await mine_mentions_batch(session, start_id=current, limit=args.batch_size)
            for key in ("archive_seen", "mentions_seen", "inserted"):
                stats_total[key] += int(stats.get(key, 0))
            current = int(stats.get("max_id", current))
            stats_total["max_id"] = current
            if args.apply:
                await session.commit()
            else:
                await session.rollback()
            if int(stats.get("archive_seen", 0)) == 0:
                break
    stats_total["mode"] = "apply" if args.apply else "dry-run"
    print(json.dumps(stats_total, ensure_ascii=False, indent=2))
    await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-id", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-batches", type=int, default=10)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
