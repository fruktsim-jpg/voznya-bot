#!/usr/bin/env python3
"""Сжать Telegram Desktop JSON export в долгую память Друна.

По умолчанию dry-run. Источник — `result.json` из Telegram Desktop.

Примеры:
    python -m scripts.drun_ingest_telegram_export --path ../docs/ChatExport_2026-06-21/result.json --dry-run --no-llm
    python -m scripts.drun_ingest_telegram_export --path /app/imports/result.json --apply --llm --max-chunks 0

`--max-chunks 0` означает все чанки. Без `--llm` пишутся только дешёвые
детерминированные факты: активность, алиасы, повторяющиеся фразы/мемы. С `--llm`
дополнительно извлекаются стиль, локальные мемы, отношения и эпизоды из текста.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.features.drun.telegram_export_ingest import (  # noqa: E402
    apply_proposals,
    build_deterministic_proposals,
    distill_export_chunks,
    filter_messages,
    load_export_messages,
)


async def _amain(args: argparse.Namespace) -> int:
    loaded_messages = load_export_messages(args.path)
    exclude_ids = {int(x) for x in args.exclude_user_id}
    messages = filter_messages(loaded_messages, exclude_user_ids=exclude_ids)
    proposals = build_deterministic_proposals(messages)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        if args.llm:
            max_chunks = None if args.max_chunks == 0 else args.max_chunks
            proposals.extend(await distill_export_chunks(
                session,
                messages,
                chunk_size=args.chunk_size,
                max_chunks=max_chunks,
            ))
        stats = await apply_proposals(session, proposals, dry_run=not args.apply)
        if args.apply:
            await session.commit()
        else:
            await session.rollback()

    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "loaded_messages": len(loaded_messages),
        "messages": len(messages),
        "excluded_user_ids": sorted(exclude_ids),
        "proposals": len(proposals),
        "stats": stats,
        "sample": [p.as_dict() for p in proposals[: args.sample]],
    }, ensure_ascii=False, indent=2, default=str))
    await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="path to Telegram Desktop result.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    llm = parser.add_mutually_exclusive_group()
    llm.add_argument("--llm", action="store_true", help="run LLM distillation over chunks")
    llm.add_argument("--no-llm", action="store_true", help="only deterministic proposals")
    parser.add_argument("--chunk-size", type=int, default=90)
    parser.add_argument("--max-chunks", type=int, default=80, help="0 = all chunks")
    parser.add_argument("--sample", type=int, default=25)
    parser.add_argument(
        "--exclude-user-id",
        action="append",
        default=["8785112116"],
        help="Telegram user_id to exclude from learning; repeatable. Defaults to Drun bot id.",
    )
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
