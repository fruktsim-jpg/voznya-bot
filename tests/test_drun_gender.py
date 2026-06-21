"""Тесты эвристики пола (чистая логика, без БД/LLM).

Сторожит фолбэк, который добирает пол игрока из его собственных реплик, когда
LLM вернул 'unknown'. Консервативность важнее охвата: при сомнении — 'unknown'.
"""
from __future__ import annotations

from app.features.drun import gender as g


def test_female_by_verb_endings():
    msgs = ["я вчера сделала всё", "я так устала", "я пошла спать"]
    assert g.infer_gender(msgs) == "female"


def test_male_by_verb_endings():
    msgs = ["я сделал таску", "я устал как пёс", "я пошёл фармить"]
    assert g.infer_gender(msgs) == "male"


def test_explicit_self_label_female():
    msgs = ["я девушка вообще-то", "что не так"]
    assert g.infer_gender(msgs) == "female"


def test_unknown_when_no_signal():
    assert g.infer_gender(["норм", "ок", "лол"]) == "unknown"
    assert g.infer_gender([]) == "unknown"
    assert g.infer_gender(["", "   "]) == "unknown"


def test_unknown_when_conflicting():
    # Один «сделала» против одного «сделал» — слабо и противоречиво → unknown.
    assert g.infer_gender(["я сделала", "я сделал"]) == "unknown"


def test_third_person_not_counted_as_self():
    # Чужой род в пересказе («она сказала») не должен делать игрока женщиной.
    msgs = ["она сказала что придёт", "он ушёл", "они опоздали"]
    assert g.infer_gender(msgs) == "unknown"
