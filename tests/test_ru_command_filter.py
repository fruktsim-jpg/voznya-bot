from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.core.filters import RuCommand, looks_like_known_command


def _run(coro):
    return asyncio.run(coro)


def _message(text: str):
    return SimpleNamespace(text=text, caption=None)


def test_bare_top_with_sentence_tail_is_not_command():
    result = _run(RuCommand("топ")(_message("топ погода сегодня")))

    assert result is False
    assert looks_like_known_command("топ погода сегодня") is False


def test_bare_top_with_numeric_page_is_command():
    result = _run(RuCommand("топ")(_message("топ 2")))

    assert result == {"command_args": "2"}
    assert looks_like_known_command("топ 2") is True


def test_slash_top_keeps_free_args_for_compatibility():
    result = _run(RuCommand("топ")(_message("/топ погода сегодня")))

    assert result == {"command_args": "погода сегодня"}
    assert looks_like_known_command("/топ погода сегодня") is True


def test_bare_profile_rejects_long_sentence_tail():
    result = _run(RuCommand("профиль")(_message("профиль погода сегодня")))

    assert result is False
    assert looks_like_known_command("профиль погода сегодня") is False


def test_bare_duel_accepts_target_and_amount():
    result = _run(RuCommand("бой")(_message("бой @vasya 50")))

    assert result == {"command_args": "@vasya 50"}
    assert looks_like_known_command("бой @vasya 50") is True


def test_bare_drun_keeps_free_text():
    result = _run(RuCommand("друн")(_message("друн погода сегодня")))

    assert result == {"command_args": "погода сегодня"}
    assert looks_like_known_command("друн погода сегодня") is True
