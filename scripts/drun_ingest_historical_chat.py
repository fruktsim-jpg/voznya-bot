#!/usr/bin/env python3
"""Собрать историческую память Друна из уже импортированных Combot-таблиц.

По умолчанию dry-run: печатает предложения и ничего не пишет. Для записи в
``ai_memories`` нужен явный ``--apply``. Скрипт не трогает economy/game state.

Запуск:
    python -m scripts.drun_ingest_historical_chat --dry-run
    python -m scripts.drun_ingest_historical_chat --apply --limit 80
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.features.drun.history_ingest import apply_proposals, build_proposals  # noqa: E402


async def _amain(args: argparse.Namespace) -> int:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        proposals = await build_proposals(
            session,
            limit=args.limit,
            min_messages=args.min_messages,
        )
        stats = await apply_proposals(session, proposals, dry_run=not args.apply)
        if args.apply:
            await session.commit()
        else:
            await session.rollback()

    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "stats": stats,
        "sample": [p.as_dict() for p in proposals[: args.sample]],
    }, ensure_ascii=False, indent=2, default=str))
    await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="ничего не писать (default)")
    mode.add_argument("--apply", action="store_true", help="записать новые ai_memories")
    parser.add_argument("--limit", type=int, default=50, help="сколько топ-активных игроков обработать")
    parser.add_argument("--min-messages", type=int, default=100, help="минимум Combot-сообщений игрока")
    parser.add_argument("--sample", type=int, default=20, help="сколько предложений вывести в sample")
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
