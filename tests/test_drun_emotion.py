"""Тесты стойкого НАСТРОЕНИЯ друна (emotion) и отложенных реакций (deferral).

Чистые ядра без БД: затухание/толчки эмоции и отбор «дозревших» отложек.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.features.drun import deferral, emotion
from app.features.drun.emotion import Emotion


# --- emotion: затухание и толчки ---------------------------------------------


def test_decay_pulls_toward_neutral():
    v, a = emotion.decay(0.8, 0.8, hours=emotion._VALENCE_HALFLIFE_H)
    # За один полураспад valence падает примерно вдвое.
    assert 0.35 < v < 0.45


def test_arousal_decays_faster_than_valence():
    v, a = emotion.decay(0.8, 0.8, hours=2.0)
    assert a < v  # arousal стекает быстрее (короче полураспад)


def test_decay_zero_hours_is_clamp_only():
    v, a = emotion.decay(1.5, -0.3, hours=0)
    assert v == 1.0      # зажато в [-1,1]
    assert a == 0.0      # arousal зажат в [0,1]


def test_decay_kills_tiny_tail():
    v, a = emotion.decay(0.01, 0.01, hours=0.01)
    assert v == 0.0 and a == 0.0


def test_nudge_clamps():
    v, a = emotion.nudge(0.95, 0.95, (0.5, 0.5))
    assert v == 1.0 and a == 1.0
    v, a = emotion.nudge(-0.95, 0.05, (-0.5, -0.5))
    assert v == -1.0 and a == 0.0


def test_hostile_nudge_lowers_valence_raises_arousal():
    v, a = emotion.nudge(0.0, 0.0, emotion.NUDGE_HOSTILE)
    assert v < 0.0
    assert a > 0.0


def test_label_keyed_states():
    assert Emotion(-0.4, 0.7).label == "ВЗВИНЧЕН"
    assert Emotion(0.4, 0.7).label == "НА КУРАЖЕ"
    assert Emotion(-0.5, 0.1).label == "ХМУРЫЙ"
    assert Emotion(0.5, 0.1).label == "В ДУХЕ"
    assert Emotion(0.0, 0.0).label == "ВЯЛЫЙ"


def test_directive_nonempty_for_charged_states():
    assert Emotion(-0.4, 0.7).directive()
    assert Emotion(0.4, 0.7).directive()
    # Ровное настроение не добавляет шума в промпт.
    assert Emotion(0.0, 0.4).directive() == ""


# --- deferral: отбор дозревших -----------------------------------------------


def _item(age_min: float, name: str = "x") -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=age_min)).isoformat()
    return {"user_id": 1, "name": name, "gist": "g", "kind": "roast", "ts": ts}


def test_partition_skips_too_fresh():
    now = datetime.now(timezone.utc)
    due, kept = deferral._partition_due([_item(1)], now)
    assert due is None
    assert len(kept) == 1  # ещё не дозрела, держим


def test_partition_drops_stale():
    now = datetime.now(timezone.utc)
    due, kept = deferral._partition_due([_item(10_000)], now)
    assert due is None
    assert kept == []  # протухла — выкинули


def test_partition_returns_due_and_keeps_rest():
    now = datetime.now(timezone.utc)
    items = [_item(30, "old"), _item(20, "mid"), _item(1, "fresh")]
    due, kept = deferral._partition_due(items, now)
    assert due is not None
    assert due["name"] == "old"     # самая старая дозревшая первой
    # Остальные дозревшие/свежие остаются в очереди.
    names = {k["name"] for k in kept}
    assert "fresh" in names and "mid" in names


def test_partition_drops_broken_records():
    now = datetime.now(timezone.utc)
    due, kept = deferral._partition_due([{"name": "broken"}], now)
    assert due is None
    assert kept == []
