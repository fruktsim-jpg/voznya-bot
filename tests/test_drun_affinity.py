"""Тесты аффинити друна (чистые функции: тон, дельта, затухание, ярлыки)."""

from __future__ import annotations

from app.features.drun import affinity as af


def test_sentiment_warm_and_hostile():
    assert af.score_sentiment("спасибо друн, красава") > 0
    assert af.score_sentiment("заткнись тупой бот") < 0
    assert af.score_sentiment("ну сколько там по дуэлям") == 0


def test_sentiment_clamped():
    assert af.score_sentiment("спасибо спасибо лучший топ респект") == 2
    assert af.score_sentiment("тупой дебил урод сдохни тварь") == -2


def test_apply_delta_hostile_hits_harder_than_warm():
    # Симметричный тон, но обида копится быстрее доверия.
    warm = af.apply_delta(0, 1)
    cold = af.apply_delta(0, -1)
    assert warm > 0 and cold < 0
    assert abs(cold) > abs(warm)


def test_apply_delta_clamped_to_bounds():
    assert af.apply_delta(98, 2) == 100
    assert af.apply_delta(-98, -2) == -100


def test_decay_moves_toward_neutral():
    assert af.apply_delta(0, 0) == 0
    assert af._decayed(40, 5) < 40
    assert af._decayed(40, 5) >= 0
    assert af._decayed(-40, 5) > -40
    # Очень старое отношение полностью затухает.
    assert af._decayed(10, 100) == 0


def test_labels_across_range():
    assert af.Affinity(-80).label == "ЛИЧНЫЙ ВРАГ"
    assert af.Affinity(-30).label == "НЕДРУГ"
    assert af.Affinity(0).label == "НЕЙТРАЛ"
    assert af.Affinity(40).label == "ПРИЯТЕЛЬ"
    assert af.Affinity(80).label == "КОРЕШ"


def test_directive_nonempty_for_each_label():
    for s in (-80, -30, 0, 40, 80):
        assert af.Affinity(s).directive
