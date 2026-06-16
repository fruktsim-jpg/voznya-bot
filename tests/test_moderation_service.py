"""Тесты чистой логики модерации: парсинг длительностей и форматирование.

Эти функции не ходят в БД/Telegram, поэтому проверяются напрямую.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.utils import now_utc
from app.features.moderation import service
from app.settings import moderation as mod_settings


def test_parse_duration_units():
    assert service.parse_duration("10m") == 600
    assert service.parse_duration("2h") == 7200
    assert service.parse_duration("1d") == 86400
    assert service.parse_duration("1w") == 7 * 86400


def test_parse_duration_russian_units():
    assert service.parse_duration("10м") == 600
    assert service.parse_duration("2ч") == 7200
    assert service.parse_duration("1д") == 86400


def test_parse_duration_bare_number_is_minutes():
    # Голое число трактуем как минуты — привычно для модерации.
    assert service.parse_duration("5") == 300


def test_parse_duration_zero_is_permanent():
    assert service.parse_duration("0") is service.PERMANENT
    assert service.parse_duration("навсегда") is service.PERMANENT
    assert service.parse_duration("forever") is service.PERMANENT


def test_parse_duration_invalid_returns_none():
    # «Не похоже на длительность» → None (вызывающий берёт дефолт).
    assert service.parse_duration("abc") is None
    assert service.parse_duration("") is None
    assert service.parse_duration(None) is None
    assert service.parse_duration("10x") is None


def test_resolve_until_permanent_is_none():
    assert service.resolve_until(service.PERMANENT, 3600) is None


def test_resolve_until_default_when_not_specified():
    before = now_utc()
    until = service.resolve_until(None, 3600)
    assert until is not None
    delta = until - before
    # Должно быть около часа (с запасом на время выполнения).
    assert timedelta(seconds=3590) <= delta <= timedelta(seconds=3610)


def test_resolve_until_explicit_seconds():
    before = now_utc()
    until = service.resolve_until(600, mod_settings.DEFAULT_MUTE_SECONDS)
    assert until is not None
    delta = until - before
    assert timedelta(seconds=590) <= delta <= timedelta(seconds=610)


def test_format_duration():
    assert service.format_duration(None) == "навсегда"
    assert service.format_duration(0) == "навсегда"
    assert service.format_duration(600) == "10 мин"
    assert service.format_duration(7200) == "2 ч"
    assert service.format_duration(86400) == "1 д"
    assert service.format_duration(90060) == "1 д 1 ч 1 мин"
