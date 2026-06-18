"""Тесты классификатора активности чата (governor.classify) — чистая логика."""

from __future__ import annotations

from app.features.drun.governor import Pulse, classify


def test_dead_chat_triggers_stir():
    v = classify(total_msgs=1, speakers=1, bot_directed=0)
    assert v.pulse is Pulse.DEAD
    assert v.should_stir is True
    assert v.may_autopost is True


def test_hot_chat_blocks_autopost():
    v = classify(total_msgs=40, speakers=6, bot_directed=3)
    assert v.pulse is Pulse.HOT
    assert v.may_autopost is False
    assert v.throttle is False


def test_abuse_when_one_user_spams_bot():
    # Объём есть, говорят 1-2 человека, и почти всё — в адрес бота.
    v = classify(total_msgs=20, speakers=2, bot_directed=15)
    assert v.pulse is Pulse.ABUSE
    assert v.throttle is True
    assert v.may_autopost is False


def test_many_speakers_mentioning_bot_is_hot_not_abuse():
    # Много людей и много реплик — это живой чат, а не абуз, даже с упоминаниями.
    v = classify(total_msgs=40, speakers=8, bot_directed=25)
    assert v.pulse is Pulse.HOT


def test_normal_chat_allows_soft_autopost():
    v = classify(total_msgs=10, speakers=4, bot_directed=2)
    assert v.pulse is Pulse.NORMAL
    assert v.may_autopost is True
    assert v.should_stir is False
    assert v.throttle is False


def test_empty_chat_is_dead():
    v = classify(total_msgs=0, speakers=0, bot_directed=0)
    assert v.pulse is Pulse.DEAD
