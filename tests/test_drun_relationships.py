"""Тесты графа связей друна (чистая логика: близость корешей + дедуп по людям).

Покрывают P.3 («все связаны с одним человеком»): нормировку «корешей» на
болтливость + временное окно диалога и схлопывание рёбер по человеку, чтобы один
доминирующий игрок не занимал все слоты топа.
"""

from __future__ import annotations

from app.features.drun import relationships as rel
from app.features.drun.relationships import RelEdge


def _seq(*items: tuple[int, float]) -> list[tuple[int, float]]:
    """Хелпер: последовательность (автор, время_сек)."""
    return list(items)


def test_score_buddies_rewards_real_dialogue():
    # Игрок 1 чередует реплики с 2 вплотную по времени — это диалог.
    seq = []
    t = 0.0
    for _ in range(6):
        seq.append((1, t)); t += 30
        seq.append((2, t)); t += 30
    cnt = rel._score_buddies(seq, user_id=1)
    assert cnt.get(2, 0) > 0


def test_score_buddies_ignores_far_apart_in_time():
    # Те же чередования, но с разрывом в часы — это не диалог.
    seq = []
    t = 0.0
    for _ in range(6):
        seq.append((1, t)); t += 7200  # 2 часа
        seq.append((2, t)); t += 7200
    cnt = rel._score_buddies(seq, user_id=1)
    assert cnt.get(2, 0) == 0


def test_score_buddies_loud_speaker_does_not_dominate():
    # Болтун 99 пишет МЕЖДУ всеми, а 2 реально переписывается с игроком 1.
    # Без нормировки 99 стал бы «корешом №1»; с нормировкой — 2 не ниже 99.
    seq = []
    t = 0.0
    # реальный диалог 1<->2
    for _ in range(8):
        seq.append((1, t)); t += 20
        seq.append((2, t)); t += 20
    # болтун 99 мелькает рядом со всеми поверх потока
    noisy = []
    t2 = 10.0
    for _ in range(60):
        noisy.append((99, t2)); t2 += 20
    merged = sorted(seq + noisy, key=lambda x: x[1])
    cnt = rel._score_buddies(merged, user_id=1)
    # настоящий собеседник должен котироваться не ниже болтуна
    assert cnt.get(2, 0) >= cnt.get(99, 0)


def test_dedupe_keeps_distinct_people():
    # Один человек (id=7) попал в три вида — должен занять ОДИН слот.
    edges = [
        RelEdge(7, "Маша", "buddy", strength=8),
        RelEdge(7, "Маша", "gifter", strength=5),
        RelEdge(7, "Маша", "ally", strength=3),
        RelEdge(8, "Петя", "buddy", strength=4),
        RelEdge(9, "Вася", "rival", strength=6),
    ]
    out = rel._dedupe_by_person(edges, max_edges=6)
    ids = [e.other_id for e in out]
    assert ids.count(7) == 1
    assert set(ids) == {7, 8, 9}


def test_dedupe_picks_strongest_kind_per_person():
    # У id=7 вражда (foe) приоритетнее кореша по _KIND_PRIORITY.
    edges = [
        RelEdge(7, "Маша", "buddy", strength=20),
        RelEdge(7, "Маша", "foe", strength=2),
    ]
    out = rel._dedupe_by_person(edges, max_edges=6)
    assert len(out) == 1
    assert out[0].kind == "foe"


def test_dedupe_respects_max_edges_with_distinct_people():
    edges = [RelEdge(i, f"u{i}", "buddy", strength=i) for i in range(1, 11)]
    out = rel._dedupe_by_person(edges, max_edges=4)
    assert len(out) == 4
    assert len({e.other_id for e in out}) == 4
    # самые сильные по силе
    assert {e.other_id for e in out} == {10, 9, 8, 7}
