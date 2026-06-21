from __future__ import annotations

from scripts.memory_doctor import MemoryRow, _build_cleanup_plan


def _row(
    mid: int,
    fact: str,
    *,
    subject_id: int | None = 10,
    weight: int = 1,
) -> MemoryRow:
    return MemoryRow(
        id=mid,
        subject_id=subject_id,
        kind="fact",
        fact=fact,
        weight=weight,
        source="telegram_export",
    )


def test_cleanup_plan_keeps_highest_weight_duplicate():
    plan = _build_cleanup_plan([
        _row(1, "хинт", weight=1),
        _row(2, " Хинт. ", weight=3),
        _row(3, "хинт", weight=2),
    ])

    assert len(plan) == 1
    assert plan[0].keep_id == 2
    assert plan[0].delete_ids == (1, 3)
    assert plan[0].norm_fact == "хинт"


def test_cleanup_plan_collapses_punctuation_only_sim_one_near_dup():
    plan = _build_cleanup_plan([
        _row(1, "написал в школу, что заболел", weight=1),
        _row(2, "написал в школу что заболел", weight=2),
    ])

    assert len(plan) == 1
    assert plan[0].keep_id == 2
    assert plan[0].delete_ids == (1,)


def test_cleanup_plan_tie_keeps_oldest_row():
    plan = _build_cleanup_plan([
        _row(10, "одинаковый факт", weight=2),
        _row(11, "одинаковый факт", weight=2),
    ])

    assert plan[0].keep_id == 10
    assert plan[0].delete_ids == (11,)


def test_cleanup_plan_does_not_merge_different_subjects_or_near_dups():
    plan = _build_cleanup_plan([
        _row(1, "хинт", subject_id=10),
        _row(2, "хинт", subject_id=11),
        _row(3, "любит pgvector и поиск", subject_id=10),
        _row(4, "любит pgvector и быстрый поиск", subject_id=10),
    ])

    assert plan == []
