"""Тесты анти-повтора (стоп-темы) и детектора фактических вопросов.

Чистая логика без БД/сети. Сторожит две правки против «кринжа» и галлюцинаций:
* anti-repeat ловит ЗАЛИПАНИЕ на теме (слово в 3+ из последних реплик), а не
  только дословные повторы;
* websearch.looks_factual отделяет вопросы про реальный мир (погода/курс) от
  внутренних тем Возни и обычной болтовни.
"""

from __future__ import annotations

from app.features.drun import antirepeat as ar
from app.features.drun import websearch as web


def test_antirepeat_flags_sticky_topic():
    # «зарплата» в разных формах в 3 из 4 реплик — грубый стем должен их слить
    # и пометить тему как зажёванную (морфология не должна дробить залипание).
    posts = [
        "опять про зарплату свою ноешь",
        "да забей ты на зарплату уже",
        "зарплата зарплата, других тем нет?",
        "пошли в казино лучше",
    ]
    data = ar.overused(posts)
    assert any(t.startswith("зарплат") for t in data["topics"])


def test_antirepeat_ignores_rare_topic():
    posts = ["привет всем", "как движ", "кто в казино", "погнали фармить"]
    data = ar.overused(posts)
    # Ничто не повторяется 3+ раз — стоп-тем нет.
    assert data["topics"] == []


def test_render_block_mentions_sticky_topics():
    posts = ["опять про танки", "танки танки", "ну танки же", "хватит"]
    block = ar.render_block(posts)
    assert "ЗАЖЁВАННЫЕ ТЕМЫ" in block


def test_looks_factual_detects_real_world_questions():
    assert web.looks_factual("какая сегодня погода в москве")
    assert web.looks_factual("сколько стоит биткоин")
    assert web.looks_factual("что такое скибиди туалет")


def test_looks_factual_ignores_internal_and_chitchat():
    # Внутренний мир Возни — свои данные, не интернет.
    assert not web.looks_factual("сколько у меня ешек на балансе")
    assert not web.looks_factual("какой у меня ммр в дуэлях")
    # Обычная болтовня — не факт-запрос.
    assert not web.looks_factual("здарова друн как сам")
