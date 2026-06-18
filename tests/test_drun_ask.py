"""Тесты read-директив друна ([[ask:...]]) — сверка фактов перед ответом.

Парсинг и вырезание директив — чистые функции (без БД). Резолв директив
проверяем через мок ``tools_read`` и фейковую сессию: нас интересует
маршрутизация verb→функция и формат итогового блока фактов, а не SQL.
"""

from __future__ import annotations

import asyncio

from app.features.drun import ask as drun_ask


def _run(coro):
    return asyncio.run(coro)


# --- Парсер (чистая логика, без БД) ------------------------------------------


def test_has_directive_detects_presence():
    assert drun_ask.has_directive("спорим [[ask:top:balance]] да")
    assert not drun_ask.has_directive("обычный ответ без сверки")


def test_parse_all_extracts_verb_and_args():
    out = drun_ask.parse_all("[[ask:rank:@vasya:mmr]]")
    assert len(out) == 1
    assert out[0].verb == "rank"
    assert out[0].arg1 == "@vasya"
    assert out[0].arg2 == "mmr"


def test_parse_all_verb_only():
    out = drun_ask.parse_all("[[ask:top]]")
    assert len(out) == 1
    assert out[0].verb == "top"
    assert out[0].arg1 == ""
    assert out[0].arg2 == ""


def test_parse_all_caps_directive_count():
    text = "".join(f"[[ask:player:@u{i}]]" for i in range(10))
    out = drun_ask.parse_all(text)
    assert len(out) == drun_ask._MAX_DIRECTIVES


def test_parse_all_case_insensitive():
    out = drun_ask.parse_all("[[ASK:Player:@Boss]]")
    assert out and out[0].verb == "player"
    assert out[0].arg1 == "@Boss"


def test_strip_directives_removes_and_trims():
    assert drun_ask.strip_directives("ответ [[ask:top:balance]] всё") == "ответ всё"
    assert drun_ask.strip_directives("[[ask:player:@x]]") == ""


def test_strip_collapses_blank_lines():
    src = "строка\n[[ask:top]]\n\n\nконец"
    out = drun_ask.strip_directives(src)
    assert "\n\n\n" not in out
    assert "ask" not in out


# --- Резолв (маршрутизация verb→tools_read, без реальной БД) -----------------


class _FakeNested:
    """Заглушка session.begin_nested() как async-context-менеджера."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def begin_nested(self):
        return _FakeNested()


def test_resolve_top_calls_describe_top(monkeypatch):
    calls = {}

    async def fake_describe_top(session, *, by="balance", limit=5):
        calls["by"] = by
        return "Топ по «баланс»: 1. Босс — 100"

    monkeypatch.setattr(drun_ask.tools_read, "describe_top", fake_describe_top)
    out = _run(drun_ask.resolve(_FakeSession(), "ща гляну [[ask:top:balance]]"))
    assert "СВЕРКА С БАЗОЙ" in out
    assert "Босс" in out
    assert calls["by"] == "balance"


def test_resolve_player_resolves_who_then_describes(monkeypatch):
    async def fake_resolve_who(session, who):
        return 777 if who == "@vasya" else None

    async def fake_describe_player(session, uid):
        assert uid == 777
        return "Вася: баланс 50, #4 по богатству"

    monkeypatch.setattr(drun_ask.tools_read, "resolve_who", fake_resolve_who)
    monkeypatch.setattr(drun_ask.tools_read, "describe_player", fake_describe_player)
    out = _run(drun_ask.resolve(_FakeSession(), "[[ask:player:@vasya]]"))
    assert "Вася: баланс 50" in out


def test_resolve_unknown_player_reports_not_found(monkeypatch):
    async def fake_resolve_who(session, who):
        return None

    monkeypatch.setattr(drun_ask.tools_read, "resolve_who", fake_resolve_who)
    out = _run(drun_ask.resolve(_FakeSession(), "[[ask:balance:@ghost]]"))
    assert "не найден" in out


def test_resolve_no_directive_returns_empty():
    out = _run(drun_ask.resolve(_FakeSession(), "просто ответ"))
    assert out == ""


def test_resolve_swallows_directive_failure(monkeypatch):
    async def boom(session, *, by="balance", limit=5):
        raise RuntimeError("db down")

    monkeypatch.setattr(drun_ask.tools_read, "describe_top", boom)
    # Сбой одной директивы не должен ронять resolve — вернёт пусто.
    out = _run(drun_ask.resolve(_FakeSession(), "[[ask:top]]"))
    assert out == ""
