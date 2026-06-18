"""Тесты Тёмного друна без БД: пост-фильтр вывода и каталог world_events.

Чистая логика, не требует SQLAlchemy/aiogram. Сторожит две вещи:
* фильтр режет «корпоративные» фразы и нормализует длину/обёртки;
* каталог событий мира самосогласован (severity задан для каждого типа, веса
  в допустимом диапазоне 0..3).
"""

from __future__ import annotations

from app.features.drun import filter as drun_filter
from app.services import world_events as we


def test_filter_detects_banned_phrases():
    assert drun_filter.has_banned("Уважаемый пользователь, добро пожаловать")
    assert drun_filter.has_banned("Как ИИ, я не могу помочь")
    assert not drun_filter.has_banned("шо за движ в чате, народ")


def test_filter_clean_strips_code_fences_and_quotes():
    assert drun_filter.clean('```\nтекст\n```') == "текст"
    assert drun_filter.clean('"в кавычках"') == "в кавычках"
    assert drun_filter.clean("«ёлки»") == "ёлки"


def test_filter_clean_truncates_to_max_chars():
    long = "а" * 1000
    out = drun_filter.clean(long, max_chars=50)
    assert len(out) == 50
    assert out.endswith("…")


def test_filter_strips_leaked_reply_prefix():
    # Раньше «(ты ответил X):» протекал из истории в видимый ответ — режем.
    assert drun_filter.clean("(ты ответил Вася): здарова") == "здарова"
    assert drun_filter.clean("ты ответил Коту: ну чё как") == "ну чё как"
    # И самоназвание в начале, если модель его прилепила.
    assert drun_filter.clean("Меллстрой: пошёл нахер") == "пошёл нахер"
    # Обычный текст с двоеточием внутри не трогаем.
    assert drun_filter.clean("слушай: это база") == "слушай: это база"


def test_filter_strips_leaked_addressee_tag():
    # Тег адресата из истории диалога «[ты отвечал Имя]:» не должен протечь.
    assert drun_filter.clean("[ты отвечал Петя]: здарова") == "здарова"
    assert drun_filter.clean("[ты отвечал Коту] ну чё") == "ну чё"
    # Квадратные скобки в обычном тексте не трогаем.
    assert drun_filter.clean("[важно] читай это") == "[важно] читай это"


def test_world_events_severity_catalog_is_consistent():
    # Каждый известный тип имеет severity, и она в диапазоне 0..3.
    for name, value in vars(we).items():
        if name.startswith("EVENT_") and isinstance(value, str):
            assert value in we.DEFAULT_SEVERITY, f"нет severity для {value}"
    for sev in we.DEFAULT_SEVERITY.values():
        assert 0 <= sev <= 3


def test_world_events_jackpot_and_season_are_legendary():
    assert we.DEFAULT_SEVERITY[we.EVENT_CASE_JACKPOT] == 3
    assert we.DEFAULT_SEVERITY[we.EVENT_SEASON_ENDED] == 3
    assert we.DEFAULT_SEVERITY[we.EVENT_CASE_OPEN] == 0
