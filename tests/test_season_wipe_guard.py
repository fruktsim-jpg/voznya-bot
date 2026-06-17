"""P0-guard: деструктивный вайп 0034 запускается ТОЛЬКО по явному флагу.

Регрессионный тест на самый тяжёлый production-footgun: Dockerfile гонит
``alembic upgrade head`` при каждом деплое, а 0034 — необратимый вайп экономики.
Здесь проверяем чистую функцию-гейт ``_wipe_allowed`` (без подключения к БД):
по умолчанию вайп ЗАПРЕЩЁН, разрешён лишь при ALLOW_SEASON_1_WIPE=true.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

# Загружаем модуль миграции по пути (он не пакет, импортировать обычным import
# нельзя — имя начинается с цифры).
_MIG = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "0034_season_1_wipe.py"
)
_spec = importlib.util.spec_from_file_location("mig_0034", _MIG)
assert _spec and _spec.loader
wipe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wipe)


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv("ALLOW_SEASON_1_WIPE", raising=False)
    yield


def test_wipe_blocked_by_default():
    assert wipe._wipe_allowed() is False


def test_wipe_blocked_when_empty(monkeypatch):
    monkeypatch.setenv("ALLOW_SEASON_1_WIPE", "")
    assert wipe._wipe_allowed() is False


@pytest.mark.parametrize("val", ["true", "TRUE", "True", "1", "yes", " true "])
def test_wipe_allowed_for_truthy_flag(monkeypatch, val):
    monkeypatch.setenv("ALLOW_SEASON_1_WIPE", val)
    assert wipe._wipe_allowed() is True


@pytest.mark.parametrize("val", ["false", "0", "no", "off", "nope"])
def test_wipe_blocked_for_other_values(monkeypatch, val):
    monkeypatch.setenv("ALLOW_SEASON_1_WIPE", val)
    assert wipe._wipe_allowed() is False


def test_downgrade_is_noop():
    # Вайп необратим: downgrade не должен ничего делать и не должен падать.
    assert wipe.downgrade() is None
