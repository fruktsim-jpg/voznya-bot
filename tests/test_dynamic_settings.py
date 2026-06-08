"""Тесты загрузчика динамических настроек (Admin V2, Этап 9).

Проверяем главную гарантию: если ключа нет / БД недоступна / значение битое —
возвращается дефолт из кода. Используем лёгкие фейки сессии, без реальной БД.
"""

from __future__ import annotations

import asyncio

import pytest

from app.settings import dynamic


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """Минимальный async-стаб: execute() возвращает заранее заданные строки."""

    def __init__(self, rows=None, raises=False):
        self._rows = rows or []
        self._raises = raises

    async def execute(self, *_args, **_kwargs):
        if self._raises:
            raise RuntimeError("db down")
        return _FakeResult(self._rows)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def setup_function(_):
    # Каждый тест начинает с чистого кэша, чтобы не зависеть от порядка.
    dynamic.invalidate_cache()


def test_get_int_returns_default_when_key_missing():
    session = _FakeSession(rows=[])
    assert _run(dynamic.get_int(session, "casino.max_bet", 1000)) == 1000


def test_get_int_returns_db_override():
    session = _FakeSession(rows=[("casino.max_bet", 5000)])
    assert _run(dynamic.get_int(session, "casino.max_bet", 1000)) == 5000


def test_get_int_falls_back_on_db_error():
    session = _FakeSession(raises=True)
    assert _run(dynamic.get_int(session, "casino.max_bet", 1000)) == 1000


def test_get_int_falls_back_on_bad_value():
    session = _FakeSession(rows=[("casino.max_bet", "not-a-number")])
    assert _run(dynamic.get_int(session, "casino.max_bet", 1000)) == 1000


def test_get_float_override_and_default():
    session = _FakeSession(rows=[("farm.bonus", 0.25)])
    dynamic.invalidate_cache()
    assert _run(dynamic.get_float(session, "farm.bonus", 0.1)) == pytest.approx(0.25)
    assert _run(dynamic.get_float(session, "farm.missing", 0.1)) == pytest.approx(0.1)


def test_get_bool_accepts_various_truthy():
    session = _FakeSession(rows=[("flag.on", "true"), ("flag.off", 0)])
    assert _run(dynamic.get_bool(session, "flag.on", False)) is True
    dynamic.invalidate_cache()
    session = _FakeSession(rows=[("flag.off", 0)])
    assert _run(dynamic.get_bool(session, "flag.off", True)) is False
