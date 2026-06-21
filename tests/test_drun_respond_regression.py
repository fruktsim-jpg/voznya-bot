"""Регрессия: respond() не должен падать NameError на cfg (INCIDENT 2026-06-18).

История: коммит 26b1165 ("wire perceive.Intent into econ hint") добавил в
respond() ветку `if intent_kind and cfg.econ_enabled:`, не подгрузив cfg.
Каждый адресный ответ на сообщение в чате крашился NameError -> aiogram
выбрасывал, sqlalchemy-сессия оставалась с aborted transaction, и СЛЕДУЮЩИЙ
запрос (recent_messages) каскадно валился InFailedSQLTransactionError. Тесты
до этого покрывали только `_econ_hint_for_intent` (чистый хелпер) и observe(),
но не respond() — поэтому в CI прошло.

Эти тесты дёргают respond() с заглушками БД-зависимостей (affinity, web,
governor) и подменённым generate(), и проверяют:
1. respond() ВЫЗЫВАЕТСЯ без исключений (это и есть проверка на NameError);
2. при econ_enabled=True + ROAST/HYPE — подсказка [[econ:...]] попадает в task;
3. при econ_enabled=False — подсказки нет;
4. intent_kind протекает в generate() для audit trail.

Стиль повторяет test_drun_observe_econ_wiring.py.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.features.drun import service as drun_service


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _Capture:
    kwargs: dict[str, Any] = field(default_factory=dict)


class _FakeCfg:
    def __init__(self, econ_enabled: bool):
        self.econ_enabled = econ_enabled


class _FakeAffinity:
    @staticmethod
    async def record_interaction(session, asker_id, text):
        return None


class _FakeWeb:
    @staticmethod
    async def auto_context(session, text):
        return ""


@dataclass
class _Verdict:
    throttle: bool = False
    note: str = ""


class _FakeGovernor:
    @staticmethod
    async def assess(session, channel):
        return _Verdict()


@contextmanager
def _stubs(econ_enabled: bool):
    """Подменяем всё, что respond() трогает в БД/сети, с обязательным откатом.

    Помимо generate/get_config/get_prompt подменяем три ленивых импорта
    внутри respond() (affinity/websearch/governor) — иначе на None-session
    они упадут раньше, чем мы доберёмся до проверяемой ветки cfg.econ_enabled.
    """
    capture = _Capture()

    async def _fake_generate(session, **kwargs):
        capture.kwargs = dict(kwargs)
        return drun_service.GenerateResult(ok=True, text="stub")

    async def _fake_get_config(session):
        return _FakeCfg(econ_enabled)

    async def _fake_get_prompt(session, key, default):
        return default

    orig_generate = drun_service.generate
    orig_get_config = drun_service.drun_config.get_config
    orig_get_prompt = drun_service.drun_config.get_prompt

    # Подмена ленивых импортов: respond() делает `from app.features.drun
    # import affinity as drun_affinity` внутри try/except, и наши заглушки
    # должны жить в sys.modules ДО первого импорта.
    import sys
    orig_modules: dict[str, Any] = {}
    for name, mod in (
        ("app.features.drun.affinity", _FakeAffinity),
        ("app.features.drun.websearch", _FakeWeb),
        ("app.features.drun.governor", _FakeGovernor),
    ):
        orig_modules[name] = sys.modules.get(name)
        sys.modules[name] = mod  # type: ignore[assignment]

    drun_service.generate = _fake_generate  # type: ignore[assignment]
    drun_service.drun_config.get_config = _fake_get_config  # type: ignore[assignment]
    drun_service.drun_config.get_prompt = _fake_get_prompt  # type: ignore[assignment]
    try:
        yield capture
    finally:
        drun_service.generate = orig_generate  # type: ignore[assignment]
        drun_service.drun_config.get_config = orig_get_config  # type: ignore[assignment]
        drun_service.drun_config.get_prompt = orig_get_prompt  # type: ignore[assignment]
        for name, mod in orig_modules.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def test_respond_no_intent_does_not_crash():
    """Базовый случай: ответ без intent_kind. Падал NameError до фикса."""
    with _stubs(econ_enabled=True) as cap:
        result = _run(drun_service.respond(
            session=None,
            asker_id=1,
            asker_name="Вася",
            text="как дела",
        ))
    assert result.ok is True
    assert "[[econ:" not in cap.kwargs["task"]


def test_respond_roast_intent_adds_tax_hint_when_enabled():
    with _stubs(econ_enabled=True) as cap:
        _run(drun_service.respond(
            session=None,
            asker_id=1,
            asker_name="Вася",
            text="я в топе, ты ничтожество",
            intent_kind="roast",
        ))
    task = cap.kwargs["task"]
    assert "[[econ:tax" in task


def test_respond_hype_intent_adds_grant_hint():
    with _stubs(econ_enabled=True) as cap:
        _run(drun_service.respond(
            session=None,
            asker_id=2,
            asker_name="Петя",
            text="взял х100 на казике!",
            intent_kind="hype",
        ))
    assert "[[econ:grant" in cap.kwargs["task"]


def test_respond_econ_disabled_no_hint_even_with_intent():
    """Власть выключена -> подсказки нет, но и краша нет (главное)."""
    with _stubs(econ_enabled=False) as cap:
        result = _run(drun_service.respond(
            session=None,
            asker_id=1,
            asker_name="Вася",
            text="наезд",
            intent_kind="roast",
        ))
    assert result.ok is True
    assert "[[econ:" not in cap.kwargs["task"]


def test_respond_threads_intent_kind_to_generate():
    """intent_kind должен дойти до generate() для audit trail в econ.apply."""
    with _stubs(econ_enabled=True) as cap:
        _run(drun_service.respond(
            session=None,
            asker_id=1,
            asker_name="Вася",
            text="привет",
            intent_kind="hype",
        ))
    assert cap.kwargs["intent_kind"] == "hype"
    # respond() передаёт автора как subject_id, а asker_id не выставляет:
    # grant адресату считается инициативой друна. Пользовательские econ-токены
    # режутся sanitize_user_text() до LLM, поэтому self-grant-блок не нужен на
    # обычном пути respond и не должен ломать честную подачку.
    assert cap.kwargs["subject_id"] == 1
    assert cap.kwargs.get("asker_id") is None


def test_respond_unknown_intent_no_hint_no_crash():
    """Неизвестный intent (опечатка/новый код) — мягкая деградация, не краш."""
    with _stubs(econ_enabled=True) as cap:
        result = _run(drun_service.respond(
            session=None,
            asker_id=1,
            asker_name="Вася",
            text="что-то",
            intent_kind="totally_new_kind",
        ))
    assert result.ok is True
    assert "[[econ:" not in cap.kwargs["task"]
