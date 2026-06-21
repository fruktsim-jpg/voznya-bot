"""Memory doctor: показывает дубли/шум/перекос в ``ai_memories``.

Запуск:
    docker compose exec -T bot python -m scripts.memory_doctor
    docker compose exec -T bot python -m scripts.memory_doctor --source telegram_export --top 20

По умолчанию ничего не пишет и не удаляет — только диагностика, чтобы видеть, не
зашумлена ли долгосрочная память после больших импортов. ``--apply`` включает
только безопасную чистку: схлопывает точные дубли и near-dup пары с sim=1.0,
оставляя строку с максимальным weight.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import delete, func, select

from app.core.db import dispose_engine, get_sessionmaker
from app.models import AiMemory

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


@dataclass(frozen=True)
class MemoryRow:
    id: int
    subject_id: int | None
    kind: str
    fact: str
    weight: int
    source: str | None


@dataclass(frozen=True)
class CleanupGroup:
    reason: str
    keep_id: int
    delete_ids: tuple[int, ...]
    ids: tuple[int, ...]
    subject_id: int | None
    norm_fact: str


def _norm_fact(text: str) -> str:
    """Грубая нормализация факта для поиска дублей: lower, схлоп пробелов."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _fact_signature(text: str) -> tuple[str, ...]:
    """Token-level signature for safe sim=1.0 duplicates.

    This ignores punctuation/quotes/case, but keeps word order and all tokens.
    Examples: ``Хинт`` vs ``хинт.`` and ``что, заболел`` vs ``что заболел``.
    """
    return tuple(_WORD_RE.findall((text or "").lower()))


def _shingle(text: str, k: int = 4) -> set[str]:
    """Множество словесных k-шинглов для оценки почти-дублей (Jaccard)."""
    words = _WORD_RE.findall((text or "").lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _keep_key(row: MemoryRow) -> tuple[int, int]:
    """Prefer the strongest memory, then the oldest stable row id."""
    return (int(row.weight or 0), -int(row.id))


def _build_cleanup_plan(rows: Iterable[MemoryRow]) -> list[CleanupGroup]:
    """Build safe duplicate cleanup plan without touching storage.

    Safe means only groups whose normalized facts or full token signatures are
    equal. That covers exact duplicates and the reported punctuation/case-only
    sim=1.0 near-dups. We deliberately do not merge looser near-duplicates here.
    """
    by_key: dict[tuple[int | None, tuple[str, ...]], list[MemoryRow]] = defaultdict(list)
    for row in rows:
        signature = _fact_signature(row.fact)
        if signature:
            by_key[(row.subject_id, signature)].append(row)

    groups: list[CleanupGroup] = []
    for (subject_id, signature), items in by_key.items():
        if len(items) < 2:
            continue
        keep = max(items, key=_keep_key)
        delete_ids = tuple(sorted(row.id for row in items if row.id != keep.id))
        norm_fact = " ".join(signature)
        groups.append(CleanupGroup(
            reason="token_signature",
            keep_id=keep.id,
            delete_ids=delete_ids,
            ids=tuple(sorted(row.id for row in items)),
            subject_id=subject_id,
            norm_fact=norm_fact,
        ))

    groups.sort(
        key=lambda g: (
            g.subject_id is not None,
            g.subject_id or 0,
            g.norm_fact,
            g.keep_id,
        )
    )
    return groups


async def main(
    source: str | None,
    top: int,
    near_threshold: float,
    apply: bool,
) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        stmt = select(
            AiMemory.id, AiMemory.subject_id, AiMemory.kind,
            AiMemory.fact, AiMemory.weight, AiMemory.source,
        )
        if source:
            stmt = stmt.where(AiMemory.source == source)
        rows = [MemoryRow(
            id=int(r.id),
            subject_id=r.subject_id,
            kind=r.kind,
            fact=r.fact,
            weight=int(r.weight or 0),
            source=r.source,
        ) for r in (await session.execute(stmt)).all()]

        total = len(rows)
        by_kind = Counter(r.kind for r in rows)
        by_source = Counter(r.source or "?" for r in rows)
        by_subject = Counter(r.subject_id for r in rows)

        # 1) Точные дубли по (subject_id, normalized fact).
        exact: dict[tuple, list[int]] = defaultdict(list)
        for r in rows:
            exact[(r.subject_id, _norm_fact(r.fact))].append(r.id)
        exact_dups = {k: v for k, v in exact.items() if len(v) > 1}
        exact_dup_rows = sum(len(v) - 1 for v in exact_dups.values())
        cleanup_plan = _build_cleanup_plan(rows)
        cleanup_delete_ids = [mid for group in cleanup_plan for mid in group.delete_ids]

        # 2) Почти-дубли по subject (Jaccard шинглов выше порога). Ограничиваем
        #    сравнение внутри subject, чтобы не делать O(n^2) по всей базе.
        near_pairs = []
        per_subject: dict[int, list] = defaultdict(list)
        for r in rows:
            per_subject[r.subject_id].append(r)
        for subj, items in per_subject.items():
            if len(items) < 2 or len(items) > 400:
                continue
            shingles = [(it, _shingle(it.fact)) for it in items]
            for i in range(len(shingles)):
                for j in range(i + 1, len(shingles)):
                    sim = _jaccard(shingles[i][1], shingles[j][1])
                    if sim >= near_threshold and _norm_fact(shingles[i][0].fact) != _norm_fact(shingles[j][0].fact):
                        near_pairs.append((round(sim, 2), shingles[i][0].id, shingles[j][0].id,
                                           shingles[i][0].fact[:80], shingles[j][0].fact[:80]))
        near_pairs.sort(reverse=True)

        # 3) Шумные общие факты: одинаковый текст у РАЗНЫХ subject (вероятный
        #    шаблон/мусор), и очень короткие факты.
        fact_text_counter = Counter(_norm_fact(r.fact) for r in rows)
        repeated_text = [(t, c) for t, c in fact_text_counter.most_common(top) if c > 1]
        short_facts = [r.id for r in rows if len(_norm_fact(r.fact)) < 15]

        # 4) Перекос: топ subject по числу фактов.
        top_subjects = by_subject.most_common(top)

        print(json.dumps({
            "scope_source": source or "ALL",
            "total_memories": total,
            "by_kind": by_kind.most_common(),
            "by_source": by_source.most_common(),
            "exact_duplicate_groups": len(exact_dups),
            "exact_duplicate_extra_rows": exact_dup_rows,
            "near_duplicate_pairs": len(near_pairs),
            "short_fact_rows": len(short_facts),
            "distinct_subjects": len(by_subject),
            "cleanup_mode": "apply" if apply else "dry_run",
            "cleanup_groups": len(cleanup_plan),
            "cleanup_delete_rows": len(cleanup_delete_ids),
        }, ensure_ascii=False, indent=2))

        print("\n# CLEANUP PLAN (safe normalized duplicates only):")
        for group in cleanup_plan[:top]:
            print(
                f"  keep=#{group.keep_id}; delete={list(group.delete_ids)}; "
                f"subject={group.subject_id}; fact={group.norm_fact[:100]}"
            )

        print("\n# TOP REPEATED FACT TEXTS (same text across rows):")
        for t, c in repeated_text[:top]:
            print(f"  x{c}: {t[:120]}")

        print("\n# SAMPLE NEAR-DUPLICATE PAIRS:")
        for sim, id_a, id_b, fa, fb in near_pairs[:top]:
            print(f"  sim={sim} #{id_a} ~ #{id_b}\n     A: {fa}\n     B: {fb}")

        print("\n# TOP SUBJECTS BY MEMORY COUNT:")
        for subj, c in top_subjects:
            print(f"  subject={subj}: {c} facts")

        if apply and cleanup_delete_ids:
            result = await session.execute(
                delete(AiMemory).where(AiMemory.id.in_(cleanup_delete_ids))
            )
            await session.commit()
            print(json.dumps({
                "cleanup_applied": True,
                "deleted_rows": int(result.rowcount or 0),
            }, ensure_ascii=False, indent=2))
        elif apply:
            print(json.dumps(
                {"cleanup_applied": True, "deleted_rows": 0},
                ensure_ascii=False,
                indent=2,
            ))

    await dispose_engine()


def _cli() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default=None, help="filter by AiMemory.source (e.g. telegram_export)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--near-threshold", type=float, default=0.6)
    p.add_argument("--apply", action="store_true", help="delete safe normalized duplicate rows")
    a = p.parse_args()
    asyncio.run(main(a.source, a.top, a.near_threshold, a.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
