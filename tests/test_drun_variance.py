"""Тесты слоя ВАРИАТИВНОСТИ реплик друна (variance).

Чистая логика стиля без БД/LLM: проверяем, что профиль реагирует на сигналы и
что рандом даёт реальный разброс (лекарство от однообразия).
"""

from __future__ import annotations

import random

from app.features.drun import variance
from app.features.drun.variance import Length


def test_profile_in_valid_ranges():
    rng = random.Random(0)
    s = variance.build_style(intent_kind="roast", rng=rng)
    assert 0.0 <= s.effort <= 1.0
    assert 0.0 <= s.sarcasm <= 1.0
    assert 0.0 <= s.aggression <= 1.0
    assert 0.0 <= s.warmth <= 1.0
    assert 0.55 <= s.temperature <= 1.25
    assert isinstance(s.length, Length)


def test_roast_is_more_aggressive_than_support_on_average():
    agg_roast, agg_support = 0.0, 0.0
    n = 400
    for i in range(n):
        agg_roast += variance.build_style(
            intent_kind="roast", rng=random.Random(i)
        ).aggression
        agg_support += variance.build_style(
            intent_kind="support", rng=random.Random(i)
        ).aggression
    assert agg_roast / n > agg_support / n + 0.2


def test_support_is_warmer_than_roast_on_average():
    w_support, w_roast = 0.0, 0.0
    n = 400
    for i in range(n):
        w_support += variance.build_style(
            intent_kind="support", rng=random.Random(i)
        ).warmth
        w_roast += variance.build_style(
            intent_kind="roast", rng=random.Random(i)
        ).warmth
    assert w_support / n > w_roast / n + 0.2


def test_length_varies_across_samples():
    # Главный признак живости: длина не залипает в одно значение.
    seen = set()
    for i in range(200):
        seen.add(
            variance.build_style(intent_kind="comment", rng=random.Random(i)).length
        )
    assert len(seen) >= 3  # как минимум три разных режима длины


def test_temperature_varies_across_samples():
    temps = {
        variance.build_style(intent_kind="comment", rng=random.Random(i)).temperature
        for i in range(50)
    }
    assert len(temps) > 10  # температура реально джиттерит, а не константа


def test_friend_affinity_raises_warmth():
    # Кореш (высокий аффинити) — в среднем теплее, чем враг.
    w_friend, w_enemy = 0.0, 0.0
    n = 300
    for i in range(n):
        w_friend += variance.build_style(
            intent_kind="comment", affinity_score=90, rng=random.Random(i)
        ).warmth
        w_enemy += variance.build_style(
            intent_kind="comment", affinity_score=-90, rng=random.Random(i)
        ).warmth
    assert w_friend / n > w_enemy / n + 0.2


def test_angry_mood_raises_aggression():
    n = 300
    agg_angry, agg_calm = 0.0, 0.0
    for i in range(n):
        agg_angry += variance.build_style(
            intent_kind="comment", mood_label="angry", mood_intensity=3,
            rng=random.Random(i),
        ).aggression
        agg_calm += variance.build_style(
            intent_kind="comment", mood_label="amused", mood_intensity=3,
            rng=random.Random(i),
        ).aggression
    assert agg_angry / n > agg_calm / n


def test_not_addressed_skews_shorter():
    # Неадресный вкид в среднем короче адресного ответа.
    def avg_len_rank(addressed: bool) -> float:
        order = {Length.TERSE: 0, Length.SHORT: 1, Length.MEDIUM: 2, Length.LONG: 3}
        total = 0
        n = 300
        for i in range(n):
            s = variance.build_style(
                intent_kind="comment", addressed=addressed, rng=random.Random(i)
            )
            total += order[s.length]
        return total / n

    assert avg_len_rank(addressed=False) < avg_len_rank(addressed=True)


def test_directive_reflects_axes():
    rng = random.Random(0)
    s = variance.build_style(intent_kind="roast", mood_label="angry",
                             mood_intensity=3, rng=rng)
    d = s.directive()
    assert "КАК ЗВУЧАТЬ" in d
    assert s.max_chars == variance._LENGTH_CAP[s.length]


def test_terse_has_tight_cap():
    cap = variance._LENGTH_CAP
    assert cap[Length.TERSE] < cap[Length.SHORT] < cap[Length.MEDIUM] < cap[Length.LONG]


def test_high_annoyance_opinion_raises_aggression():
    n = 300
    agg_annoyed, agg_calm = 0.0, 0.0
    for i in range(n):
        agg_annoyed += variance.build_style(
            intent_kind="comment", op_annoyance=90.0, rng=random.Random(i)
        ).aggression
        agg_calm += variance.build_style(
            intent_kind="comment", op_annoyance=20.0, rng=random.Random(i)
        ).aggression
    assert agg_annoyed / n > agg_calm / n + 0.15


def test_high_respect_opinion_softens_aggression():
    n = 300
    agg_respected, agg_not = 0.0, 0.0
    for i in range(n):
        agg_respected += variance.build_style(
            intent_kind="roast", op_respect=90.0, rng=random.Random(i)
        ).aggression
        agg_not += variance.build_style(
            intent_kind="roast", op_respect=20.0, rng=random.Random(i)
        ).aggression
    assert agg_respected / n < agg_not / n


def test_entertaining_opinion_raises_both_sarcasm_and_warmth():
    # «С ним весело» = игривый яд: сарказм И тепло одновременно (подъёб своего).
    n = 300
    sar_fun, warm_fun, sar_dull, warm_dull = 0.0, 0.0, 0.0, 0.0
    for i in range(n):
        fun = variance.build_style(
            intent_kind="comment", op_entertainment=90.0, rng=random.Random(i)
        )
        dull = variance.build_style(
            intent_kind="comment", op_entertainment=20.0, rng=random.Random(i)
        )
        sar_fun += fun.sarcasm
        warm_fun += fun.warmth
        sar_dull += dull.sarcasm
        warm_dull += dull.warmth
    assert sar_fun / n > sar_dull / n
    assert warm_fun / n > warm_dull / n


def test_annoyance_widens_aggression_spread():
    # «Склонность перегибать»: с тем, кто бесит, разброс агрессии шире.
    def spread(annoy: float) -> float:
        vals = [
            variance.build_style(
                intent_kind="comment", op_annoyance=annoy, rng=random.Random(i)
            ).aggression
            for i in range(400)
        ]
        mean = sum(vals) / len(vals)
        return sum((v - mean) ** 2 for v in vals) / len(vals)

    assert spread(95.0) > spread(50.0)


def test_opinion_defaults_are_neutral_noop():
    # Без переданного мнения (оси=50) поведение как раньше — обратная совместимость.
    a = variance.build_style(intent_kind="roast", rng=random.Random(7))
    b = variance.build_style(
        intent_kind="roast",
        op_annoyance=50.0, op_respect=50.0,
        op_entertainment=50.0, op_trust=50.0,
        rng=random.Random(7),
    )
    assert a.aggression == b.aggression
    assert a.sarcasm == b.sarcasm
    assert a.warmth == b.warmth

