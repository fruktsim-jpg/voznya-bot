"""Тесты эконом-проводки в спонтанном встревании (observe) — LEAP-2.

`respond()` уже умел вешать подсказку `[[econ:...]]` и пускать директиву в
econ.apply на адресный ответ. Теперь то же должно работать и для спонтанного
вкида: ROAST на хвастуна / HYPE на победителя — самый естественный повод
для `drun_tax`/`drun_grant`, и до LEAP-2 они блокировались тем, что
`observe()` не выставлял `allow_actions=True` и не подмешивал подсказку.

Проверяем БЕЗ БД и LLM: подменяем `generate()` собирающей заглушкой и
смотрим, какие аргументы пришли. Также накрываем decoupling `asker_id` от
`subject_id` (чтобы self-grant-блок в econ.apply не ловил инициативу друна
как «самоначисление» — игрок ничего не «просил»).

Async-стиль повторяет соглашение репозитория (test_duel_accept и др.):
``asyncio.run(coro)`` вместо pytest-asyncio. Заглушки навешиваются через
контекстный менеджер с обязательным откатом, чтобы они не утекали в другие
тесты, идущие в той же pytest-сессии.
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
    """Что прилетело в подменённый generate()."""

    kwargs: dict[str, Any] = field(default_factory=dict)


class _FakeCfg:
    def __init__(self, econ_enabled: bool):
        self.econ_enabled = econ_enabled


@contextmanager
def _stubs(econ_enabled: bool):
    """Контекст с обязательным откатом подмен.

    Подменяем три точки: drun_service.generate (главный сборщик аргументов),
    drun_service.drun_config.get_config (флаг econ_enabled) и .get_prompt
    (чтобы не лезть в БД за PROMPT_OBSERVATION). После теста — возвращаем
    оригиналы, иначе следом упадут другие тесты модуля.
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

    drun_service.generate = _fake_generate  # type: ignore[assignment]
    drun_service.drun_config.get_config = _fake_get_config  # type: ignore[assignment]
    drun_service.drun_config.get_prompt = _fake_get_prompt  # type: ignore[assignment]
    try:
        yield capture
    finally:
        drun_service.generate = orig_generate  # type: ignore[assignment]
        drun_service.drun_config.get_config = orig_get_config  # type: ignore[assignment]
        drun_service.drun_config.get_prompt = orig_get_prompt  # type: ignore[assignment]


def test_observe_roast_adds_econ_hint_when_enabled():
    with _stubs(econ_enabled=True) as cap:
        _run(drun_service.observe(
            session=None, subject_id=42, intent_kind="roast",
            intent_note="хвастун нарывается",
        ))
    task = cap.kwargs["task"]
    assert "[[econ:tax" in task
    assert "хвастун" in task
    # allow_actions включается, потому что есть конкретный субъект.
    assert cap.kwargs["allow_actions"] is True


def test_observe_hype_adds_grant_hint():
    with _stubs(econ_enabled=True) as cap:
        _run(drun_service.observe(session=None, subject_id=7, intent_kind="hype"))
    task = cap.kwargs["task"]
    assert "[[econ:grant" in task
    assert cap.kwargs["allow_actions"] is True


def test_observe_econ_disabled_no_hint():
    # Власть выключена — никакой подсказки в задании быть не должно (чтобы
    # модель не училась выдумывать директивы вхолостую).
    with _stubs(econ_enabled=False) as cap:
        _run(drun_service.observe(session=None, subject_id=42, intent_kind="roast"))
    task = cap.kwargs["task"]
    assert "[[econ:" not in task
    # allow_actions всё равно True (есть субъект), но без власти econ.apply
    # сам всё отсечёт — это второй слой защиты.
    assert cap.kwargs["allow_actions"] is True


def test_observe_no_subject_no_actions():
    # Без конкретного субъекта некого «облагать»/«одаривать» — actions выкл.
    with _stubs(econ_enabled=True) as cap:
        _run(drun_service.observe(session=None, subject_id=None, intent_kind="roast"))
    assert cap.kwargs["allow_actions"] is False
    assert "[[econ:" not in cap.kwargs["task"]


def test_observe_silent_intent_no_hint():
    # SILENT/COMMENT/прочее — не повод трогать чужой баланс.
    with _stubs(econ_enabled=True) as cap:
        _run(drun_service.observe(session=None, subject_id=42, intent_kind="silent"))
    assert "[[econ:" not in cap.kwargs["task"]


def test_observe_passes_asker_id_none():
    # Ключевой decoupling: игрок не обращался к друну, поэтому asker_id=None,
    # иначе econ.apply при grant=self ловил бы self-grant и блокировал.
    with _stubs(econ_enabled=True) as cap:
        _run(drun_service.observe(session=None, subject_id=42, intent_kind="hype"))
    assert cap.kwargs["asker_id"] is None
    assert cap.kwargs["subject_id"] == 42


def test_observe_threads_intent_kind():
    # intent_kind должен дойти до generate() как есть — для audit trail в
    # econ.apply meta.
    with _stubs(econ_enabled=True) as cap:
        _run(drun_service.observe(session=None, subject_id=1, intent_kind="roast"))
    assert cap.kwargs["intent_kind"] == "roast"
