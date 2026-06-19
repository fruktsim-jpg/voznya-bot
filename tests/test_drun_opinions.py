"""Тесты ядра ДОЛГОСРОЧНЫХ МНЕНИЙ друна (opinions) — LEAP-4.

Чистая математика без БД: инерция эволюции (EMA), медленное затухание к
нейтралу, детерминированные наблюдения из сигналов, классификация в социальные
роли и favorite-score для рейтинга любимчиков/врагов.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.features.drun import opinions
from app.features.drun.opinions import AXES, Opinion


def test_neutral_is_fifty_everywhere():
    op = opinions.neutral()
    for ax in AXES:
        assert op.get(ax) == 50.0
    assert op.samples == 0
    assert not op.is_formed


def test_evolve_moves_slowly_toward_target():
    axes = {ax: 50.0 for ax in AXES}
    obs = {ax: 100.0 for ax in AXES}
    after = opinions.evolve(axes, obs)
    # Один шаг EMA с малым alpha — далеко от цели (инерция мнения).
    assert all(52.0 < after[ax] < 60.0 for ax in AXES)


def test_evolve_converges_with_repetition():
    axes = {ax: 50.0 for ax in AXES}
    obs = {"respect": 100.0}
    for _ in range(60):
        axes = opinions.evolve(axes, obs)
    # Много подтверждений — мнение укрепляется почти до цели.
    assert axes["respect"] > 90.0
    # Оси без сигнала не двигаются.
    assert axes["trust"] == 50.0


def test_evolve_ignores_missing_axes():
    axes = {ax: 70.0 for ax in AXES}
    after = opinions.evolve(axes, {"trust": 0.0})
    assert after["trust"] < 70.0
    assert after["respect"] == 70.0


def test_decay_pulls_toward_neutral_slowly():
    axes = {ax: 100.0 for ax in AXES}
    after_1d = opinions.decay(axes, 1.0)
    # За один день почти не сдвинулось (мнение держится неделями).
    assert all(after_1d[ax] > 96.0 for ax in AXES)
    after_30d = opinions.decay(axes, 30.0)
    # За месяц заметно стекло к нейтралу, но ещё не достигло.
    assert all(55.0 < after_30d[ax] < 80.0 for ax in AXES)


def test_decay_zero_days_is_clamp_only():
    axes = {ax: 150.0 for ax in AXES}
    out = opinions.decay(axes, 0)
    assert all(out[ax] == 100.0 for ax in AXES)


def test_is_formed_after_enough_samples():
    assert not Opinion({ax: 50.0 for ax in AXES}, samples=4).is_formed
    assert Opinion({ax: 50.0 for ax in AXES}, samples=5).is_formed


def test_dominant_only_strong_axes():
    axes = {ax: 50.0 for ax in AXES}
    axes["respect"] = 80.0   # отклонение 30 — попадает
    axes["trust"] = 60.0     # отклонение 10 — нет
    op = Opinion(axes, samples=10)
    dom = dict(op.dominant())
    assert "respect" in dom
    assert "trust" not in dom


def test_standing_favorite_and_annoying():
    favs = {ax: 50.0 for ax in AXES}
    favs.update(trust=75.0, respect=70.0)
    assert Opinion(favs, samples=10).standing() == "ЛЮБИМЧИК"

    foe = {ax: 50.0 for ax in AXES}
    foe.update(annoyance=78.0, respect=40.0)
    assert Opinion(foe, samples=10).standing() == "БЕСИТ"


def test_standing_unformed_is_watching():
    assert Opinion({ax: 90.0 for ax in AXES}, samples=2).standing() == "ПРИСМАТРИВАЕТСЯ"


def test_directive_empty_when_neutral_or_unformed():
    assert Opinion({ax: 50.0 for ax in AXES}, samples=10).directive() == ""
    strong = {ax: 90.0 for ax in AXES}
    assert Opinion(strong, samples=2).directive() == ""  # не сложилось


def test_directive_mentions_standing_when_formed():
    axes = {ax: 50.0 for ax in AXES}
    axes.update(annoyance=80.0, respect=35.0)
    d = Opinion(axes, samples=12).directive()
    assert "БЕСИТ" in d
    assert "СЛОЖИВШЕЕСЯ МНЕНИЕ" in d


# --- observation from signals ------------------------------------------------


def test_high_winrate_raises_respect():
    obs_win = opinions.observe_from_signals(duels_won=90, duels_lost=10)
    obs_lose = opinions.observe_from_signals(duels_won=10, duels_lost=90)
    assert obs_win["respect"] > obs_lose["respect"]


def test_hostile_affinity_raises_annoyance():
    obs = opinions.observe_from_signals(affinity_score=-80)
    assert obs["annoyance"] > 60.0


def test_casino_tilt_raises_chaos_lowers_reliability():
    calm = opinions.observe_from_signals(casino_loss_streak=0, farm_streak=20)
    tilt = opinions.observe_from_signals(casino_loss_streak=8)
    assert tilt["chaos"] > calm["chaos"]
    assert tilt["reliability"] < calm["reliability"]


def test_farm_streak_raises_reliability():
    grind = opinions.observe_from_signals(farm_streak=15)
    assert grind["reliability"] > 60.0


def test_silent_player_less_entertaining():
    quiet = opinions.observe_from_signals(messages=5)
    loud = opinions.observe_from_signals(messages=400, affinity_score=40)
    assert loud["entertainment"] > quiet["entertainment"]


def test_observation_all_axes_clamped():
    obs = opinions.observe_from_signals(
        affinity_score=-100, rep_minus=50, casino_loss_streak=50,
        duels_won=0, duels_lost=200,
    )
    for ax in AXES:
        assert 0.0 <= obs[ax] <= 100.0


# --- merge_observation (decay → evolve pipeline) -----------------------------


def test_merge_observation_increments_samples():
    out = opinions.merge_observation(None, {"respect": 100.0})
    assert out["samples"] == 1
    assert "ts" in out and "axes" in out
    out2 = opinions.merge_observation(out, {"respect": 100.0})
    assert out2["samples"] == 2
    assert out2["axes"]["respect"] > out["axes"]["respect"]


def test_merge_observation_decays_stale_then_evolves():
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    raw = {"axes": {ax: 100.0 for ax in AXES}, "samples": 30, "ts": old_ts}
    out = opinions.merge_observation(raw, {"trust": 50.0})
    # 60 дней простоя сильно стянули к нейтралу, несмотря на высокий старт.
    assert out["axes"]["respect"] < 90.0


# --- favorite_score / ranking core -------------------------------------------


def test_favorite_score_positive_for_liked():
    liked = {ax: 50.0 for ax in AXES}
    liked.update(trust=80.0, respect=75.0, entertainment=70.0, annoyance=30.0)
    assert opinions.favorite_score(Opinion(liked, samples=10)) > 0


def test_favorite_score_negative_for_disliked():
    disliked = {ax: 50.0 for ax in AXES}
    disliked.update(annoyance=85.0, respect=30.0, trust=35.0)
    assert opinions.favorite_score(Opinion(disliked, samples=10)) < 0
