from __future__ import annotations

from app.features.drun import response_mode as rm


def test_question_mode_wins_over_default():
    mode = rm.classify_response_mode("слушай а как вывести подарок?")
    assert mode.name in ("help", "question")


def test_help_mode_for_howto():
    mode = rm.classify_response_mode("как заработать ешки нормально")
    assert mode.name == "help"


def test_vent_mode_for_distress():
    mode = rm.classify_response_mode("мне очень тяжело, всё надоело")
    assert mode.name == "vent"


def test_crisis_mode_priority():
    mode = rm.classify_response_mode("я не хочу жить уже")
    assert mode.name == "crisis"
    assert "8-800" in mode.directive


def test_threat_joke_mode():
    mode = rm.classify_response_mode("ща ментов вызову на тебя")
    assert mode.name == "threat_joke"


def test_joke_request_mode_avoids_generic_roast():
    mode = rm.classify_response_mode("расскажи анекдот")

    assert mode.name == "joke"
    assert "проигранные ешки" in mode.directive
    assert "сетап" in mode.directive


def test_fun_fact_mode_uses_memory_not_roast():
    mode = rm.classify_response_mode("расскажи забавный факт из чата")

    assert mode.name == "fun_fact"
    assert "забавный факт" in mode.directive
    assert "ешки/дуэли" in mode.directive


def test_aggression_mode():
    mode = rm.classify_response_mode("тупой бот заткнись")
    assert mode.name == "aggression"


def test_smalltalk_short_greeting():
    mode = rm.classify_response_mode("здарова ты тут?")
    assert mode.name in ("smalltalk", "question")


def test_default_mode_for_statement():
    mode = rm.classify_response_mode("сегодня в зале нормально потренил короче")
    assert mode.name == "default"


def test_mode_directive_returns_block():
    name, block = rm.mode_directive("как поднять денег")
    assert name == "help"
    assert "КАК ОТВЕТИТЬ ИМЕННО СЕЙЧАС" in block
