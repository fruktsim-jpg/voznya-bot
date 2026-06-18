"""Ручной триггер backfill embeddings — для верификации после деплоя.

Запускается:
    docker compose exec bot python -m scripts.embed_backfill_once

Качает модель fastembed при первом вызове (~120 МБ с HuggingFace),
эмбедит до 64 фактов, коммитит, печатает итоги. Безопасно дёргать
сколько угодно раз: джоб уже планируется в main.setup_embeddings_backfill,
это просто разовый pull для проверки.
"""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import select, text

from app.core.db import get_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from app.features.drun.embeddings import (
    backfill_missing,
    get_embedding_config,
    embed_text,
)


async def main() -> None:
    engine = get_engine()
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with sm() as session:
        cfg = await get_embedding_config(session)
        print(f"config: base_url={cfg.base_url} model={cfg.model} dim={cfg.dim} usable={cfg.usable}")

        # Probe-вызов: грузит модель, эмбедит одну строку — латентность видна сразу.
        t0 = time.monotonic()
        v = await embed_text(session, "проверка эмбеддинга друна")
        dt = (time.monotonic() - t0) * 1000
        print(f"probe embed: dt={dt:.0f}ms dim={len(v) if v else None}")

        # Сколько ждёт backfill до и после.
        before = await session.scalar(
            text("SELECT count(*) FROM ai_memories WHERE embedding IS NULL")
        )
        print(f"missing before: {before}")

        t0 = time.monotonic()
        n = await backfill_missing(session)
        dt = (time.monotonic() - t0) * 1000
        print(f"backfill round: {n} rows in {dt:.0f}ms")

        after = await session.scalar(
            text("SELECT count(*) FROM ai_memories WHERE embedding IS NULL")
        )
        embedded = await session.scalar(
            text("SELECT count(*) FROM ai_memories WHERE embedding IS NOT NULL")
        )
        print(f"missing after: {after}, embedded total: {embedded}")


if __name__ == "__main__":
    asyncio.run(main())
