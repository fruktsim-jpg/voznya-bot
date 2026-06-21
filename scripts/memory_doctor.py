"""Read-only memory doctor: показывает дубли/шум/перекос в ``ai_memories``.

Запуск:
    docker compose exec -T bot python -m scripts.memory_doctor
    docker compose exec -T bot python -m scripts.memory_doctor --source telegram_export --top 20

НИЧЕГО не пишет и не удаляет — только диагностика, чтобы видеть, не зашумлена ли
долгосрочная память после больших импортов (дубли фактов, почти-дубли, слишком
общие мемы, перекос по kind/subject). Решение о чистке принимает человек.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter, defaultdict

from sqlalchemy import func, select

from app.core.db import dispose_engine, get_sessionmaker
from app.models import AiMemory

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _norm_fact(text: str) -> str:
    """Грубая нормализация факта для поиска дублей: lower, схлоп пробелов."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


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


async def main(source: str | None, top: int, near_threshold: float) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        stmt = select(
            AiMemory.id, AiMemory.subject_id, AiMemory.kind,
            AiMemory.fact, AiMemory.weight, AiMemory.source,
        )
        if source:
            stmt = stmt.where(AiMemory.source == source)
        rows = (await session.execute(stmt)).all()

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
        }, ensure_ascii=False, indent=2))

        print("\n# TOP REPEATED FACT TEXTS (same text across rows):")
        for t, c in repeated_text[:top]:
            print(f"  x{c}: {t[:120]}")

        print("\n# SAMPLE NEAR-DUPLICATE PAIRS:")
        for sim, id_a, id_b, fa, fb in near_pairs[:top]:
            print(f"  sim={sim} #{id_a} ~ #{id_b}\n     A: {fa}\n     B: {fb}")

        print("\n# TOP SUBJECTS BY MEMORY COUNT:")
        for subj, c in top_subjects:
            print(f"  subject={subj}: {c} facts")

    await dispose_engine()


def _cli() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default=None, help="filter by AiMemory.source (e.g. telegram_export)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--near-threshold", type=float, default=0.6)
    a = p.parse_args()
    asyncio.run(main(a.source, a.top, a.near_threshold))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
