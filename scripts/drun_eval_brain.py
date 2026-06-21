#!/usr/bin/env python3
"""Evaluate Drun brain routing/retrieval without sending messages.

This is a diagnostics harness, not a benchmark. It answers: for a real phrase,
which response mode and context route are selected, whether identity resolver
finds a person, which context blocks appear, and whether economy/archive/memory
are being over-included.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.features.drun import context as drun_context  # noqa: E402
from app.features.drun import identity as drun_identity  # noqa: E402
from app.features.drun import response_mode as drun_response_mode  # noqa: E402


DEFAULT_CASES = [
    "кто такая Карина",
    "расскажи о Карине",
    "расскажи про хинта",
    "что хинт писал про pgvector",
    "помнишь когда oew с кем-то срался",
    "как заработать ешки нормально",
    "как работает архив памяти",
    "мне плохо и всё надоело",
    "ща ментов вызову на тебя",
    "здарова ты тут?",
    "у кого больше ешек",
]


@dataclass
class EvalResult:
    query: str
    response_mode: str
    context_intent: str
    include_archive: bool
    include_economy: bool
    include_identity: bool
    identity_candidates: list[dict]
    context_chars: int
    has_identity_block: bool
    has_dossier: bool
    has_archive_block: bool
    has_memory_block: bool
    has_economy_block: bool
    preview: str


def _candidate_dict(c: drun_identity.PersonCandidate) -> dict:
    return {
        "user_id": c.user_id,
        "name": c.name,
        "confidence": round(c.confidence, 3),
        "sources": c.sources[:6],
        "aliases": c.aliases[:8],
        "archive_hits": c.archive_hits,
        "memory_hits": c.memory_hits,
    }


async def eval_query(session, query: str, *, context_preview: int = 1600) -> EvalResult:
    mode = drun_response_mode.classify_response_mode(query)
    route = drun_context.classify_context_route(query, channel="chat", subject_id=None)
    candidates = await drun_identity.resolve_person(session, query, limit=5)
    ctx = await drun_context.build_context(
        session,
        channel="chat",
        subject_id=None,
        query=query,
        include_chat=True,
        include_events=True,
    )
    ctx_s = str(ctx)
    return EvalResult(
        query=query,
        response_mode=mode.name,
        context_intent=route.intent.value,
        include_archive=route.include_archive,
        include_economy=route.include_economy,
        include_identity=route.include_identity,
        identity_candidates=[_candidate_dict(c) for c in candidates],
        context_chars=len(ctx_s),
        has_identity_block="# КТО ЭТО МОЖЕТ БЫТЬ" in ctx_s,
        has_dossier="# АВТО-ДОСЬЕ ЧЕЛОВЕКА" in ctx_s,
        has_archive_block="# СЫРОЙ АРХИВ ЧАТА" in ctx_s,
        has_memory_block="# ДОЛГАЯ ПАМЯТЬ ДРУНА" in ctx_s,
        has_economy_block="# ЭКОНОМИКА" in ctx_s or "ЭКОНОМИ" in ctx_s,
        preview=ctx_s[:context_preview],
    )


async def _amain(args: argparse.Namespace) -> int:
    queries = list(args.query or [])
    if args.cases:
        data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
        if isinstance(data, list):
            queries.extend(str(x) for x in data)
        else:
            queries.extend(str(x) for x in data.get("queries", []))
    if not queries:
        queries = DEFAULT_CASES

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        results = [
            asdict(await eval_query(session, q, context_preview=args.preview))
            for q in queries
        ]

    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"\n## {r['query']}")
            print(
                f"mode={r['response_mode']} intent={r['context_intent']} "
                f"archive={r['include_archive']} economy={r['include_economy']} "
                f"identity={r['include_identity']} chars={r['context_chars']}"
            )
            print(
                f"blocks: identity={r['has_identity_block']} dossier={r['has_dossier']} "
                f"archive={r['has_archive_block']} memory={r['has_memory_block']} "
                f"economy={r['has_economy_block']}"
            )
            print("candidates:")
            for c in r["identity_candidates"][:3]:
                print(
                    f"- user_id={c['user_id']} name={c['name']} conf={c['confidence']} "
                    f"archive={c['archive_hits']} memory={c['memory_hits']} sources={','.join(c['sources'])}"
                )
            if args.show_preview:
                print("preview:")
                print(r["preview"])
    await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="*", help="queries to evaluate; defaults to built-in cases")
    parser.add_argument("--cases", help="JSON file with list or {'queries': [...]}")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--preview", type=int, default=1600)
    parser.add_argument("--show-preview", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
