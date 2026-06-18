"""Тесты связи восприятия (perceive.Intent) и эконом-власти (econ hint).

Чистая проверка хелпера `_econ_hint_for_intent`: ROAST/HYPE/SUPPORT дают
осмысленную подсказку про `[[econ:...]]`, остальные интенты — пустую строку.
LLM не зовём, БД не трогаем.
"""

from __future__ import annotations

from app.features.drun.service import _econ_hint_for_intent


def test_roast_intent_hints_tax():
    hint = _econ_hint_for_intent("roast")
    assert "tax" in hint
    assert "[[econ:tax" in hint


def test_hype_intent_hints_grant():
    hint = _econ_hint_for_intent("hype")
    assert "grant" in hint
    assert "[[econ:grant" in hint


def test_support_intent_hints_grant():
    hint = _econ_hint_for_intent("support")
    assert "grant" in hint


def test_silent_intent_no_hint():
    assert _econ_hint_for_intent("silent") == ""


def test_comment_intent_no_hint():
    # COMMENT — нейтральная реплика, не повод трогать чужой баланс.
    assert _econ_hint_for_intent("comment") == ""


def test_stir_intent_no_hint():
    assert _econ_hint_for_intent("stir") == ""


def test_unknown_intent_no_hint():
    assert _econ_hint_for_intent("totally_unknown") == ""


def test_none_intent_no_hint():
    assert _econ_hint_for_intent(None) == ""


def test_empty_intent_no_hint():
    assert _econ_hint_for_intent("") == ""


def test_intent_case_insensitive():
    # На случай, если кто-то передаст «ROAST» вместо «roast».
    assert _econ_hint_for_intent("ROAST") == _econ_hint_for_intent("roast")
