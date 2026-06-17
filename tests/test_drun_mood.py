"""Юнит-тесты классификатора настроения друна (#7).

Проверяем чистую функцию ``_classify`` без БД: сигналы → ожидаемая метка.
"""

from __future__ import annotations

from app.features.drun import mood


def test_celebratory_on_big_win():
    m = mood._classify(hot=5, celebratory=1, conflict=0, total_ev=3,
                       big_amount=100_000)
    assert m.label == mood.MOOD_CELEBRATORY


def test_chaotic_when_hot_and_conflicts():
    m = mood._classify(hot=30, celebratory=0, conflict=4, total_ev=10,
                       big_amount=0)
    assert m.label == mood.MOOD_CHAOTIC
    assert m.intensity == 3


def test_angry_on_many_conflicts():
    m = mood._classify(hot=5, celebratory=0, conflict=6, total_ev=8,
                       big_amount=0)
    assert m.label == mood.MOOD_ANGRY


def test_excited_on_busy_chat():
    m = mood._classify(hot=22, celebratory=0, conflict=0, total_ev=1,
                       big_amount=0)
    assert m.label == mood.MOOD_EXCITED


def test_suspicious_quiet_chat_active_world():
    m = mood._classify(hot=1, celebratory=0, conflict=0, total_ev=5,
                       big_amount=0)
    assert m.label == mood.MOOD_SUSPICIOUS


def test_disappointed_on_dead_silence():
    m = mood._classify(hot=0, celebratory=0, conflict=0, total_ev=0,
                       big_amount=0)
    assert m.label == mood.MOOD_DISAPPOINTED


def test_directive_mentions_label():
    m = mood.Mood(mood.MOOD_AMUSED, intensity=2, reason="движ")
    d = m.directive()
    assert "AMUSED" in d
    assert "движ" in d
