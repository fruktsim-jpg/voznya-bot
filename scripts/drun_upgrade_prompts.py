#!/usr/bin/env python3
"""Идемпотентно добавить smart-блоки в DB-промпты Друна.

По умолчанию dry-run. Для применения:
    python -m scripts.drun_upgrade_prompts --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.features.drun.prompt_upgrades import apply_prompt_upgrades  # noqa: E402


async def _amain(args: argparse.Namespace) -> int:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        stats = await apply_prompt_upgrades(session, dry_run=not args.apply)
        if args.apply:
            await session.commit()
        else:
            await session.rollback()
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "stats": stats}, ensure_ascii=False, indent=2))
    await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return asyncio.run(_amain(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
