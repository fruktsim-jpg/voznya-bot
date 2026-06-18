"""Тесты слоя восприятия/решения о вовлечении (perceive).

Это чистая детерминированная логика агентности — тестируем без БД и LLM.
"""

from __future__ import annotations

from app.features.drun import perceive
from app.features.drun.perceive import Intent


def test_silent_on_empty():
    e = perceive.decide_engagement("", chat_hot=10)
    assert e.intent is Intent.SILENT
    assert not e.wants_in


def test_aggression_triggers_roast_strongly():
    e = perceive.decide_engagement("ты лох и нищеброд", chat_hot=2)
    assert e.intent is Intent.ROAST
    assert e.urge >= 0.6
    assert e.wants_in


def test_bragging_triggers_roast():
    e = perceive.decide_engagement("изи затащил, я лучший", chat_hot=2)
    assert e.intent is Intent.ROAST


def test_distress_triggers_support():
    e = perceive.decide_engagement("всё хреново, заебало это казино", chat_hot=2)
    assert e.intent is Intent.SUPPORT


def test_boredom_triggers_stir():
    e = perceive.decide_engagement("скучно, тут есть кто живой", chat_hot=1)
    assert e.intent is Intent.STIR


def test_open_question_comments_when_not_addressed_to_other():
    e = perceive.decide_engagement(
        "кто-нибудь знает что лучше качать", chat_hot=3, addressed_other=False
    )
    assert e.intent is Intent.COMMENT
    assert e.wants_in


def test_open_question_silent_when_addressed_to_other():
    # Реплай конкретному человеку — друн не лезет в чужой диалог по этому сигналу.
    e = perceive.decide_engagement(
        "кто-нибудь подскажет", chat_hot=2, addressed_other=True
    )
    assert e.intent is Intent.SILENT


def test_quiet_neutral_chat_stays_silent():
    e = perceive.decide_engagement("ну такое, посмотрим вечером", chat_hot=1)
    assert e.intent is Intent.SILENT
    assert not e.wants_in


def test_hot_neutral_chat_weak_comment():
    e = perceive.decide_engagement("ну такое, посмотрим вечером", chat_hot=8)
    assert e.intent is Intent.COMMENT
    assert 0.0 < e.urge < 0.3


def test_drun_topic_detection():
    assert perceive.mentions_drun_topic("а друн опять налог собрал")
    assert perceive.mentions_drun_topic("это казино меня разорит")
    assert not perceive.mentions_drun_topic("пойду гулять с собакой")


def test_drun_topic_comment_intent():
    e = perceive.decide_engagement(
        "опять эти ешки кончились", chat_hot=3,
        mentions_drun_topic=True, addressed_other=False,
    )
    assert e.intent is Intent.COMMENT
