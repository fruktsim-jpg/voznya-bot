"""Регрессия: честная подачка Друна адресату не должна ловить self_grant.

Проблема: generate() подставлял asker_id=subject_id по умолчанию. Поэтому когда
модель сама писала ``[[econ:grant:...]]`` в ответе игроку, econ.apply видел
target_id == asker_id и блокировал действие как самоначисление. В чате Друн
говорил «накину ешек», директива вырезалась, а денег не двигалось.

Защита от пользовательского абуза остаётся раньше по цепочке:
respond() калечит любые ``[[econ:...]]`` во вводе игрока через sanitize_user_text(),
так что до парсера может доехать только директива, которую сгенерировала модель.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from app.features.drun import service as drun_service


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _FakeCfg:
    econ_enabled: bool = True
    usable: bool = True
    temperature: float = 0.7

    def model_for(self, role: str) -> str:
        return "fake-model"


class _NoopNested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def begin_nested(self):
        return _NoopNested()

    async def execute(self, *args, **kwargs):
        return []


@contextmanager
def _stubs(raw: str):
    calls: dict[str, Any] = {}

    async def _fake_get_config(session):
        return _FakeCfg()

    async def _fake_system(session, *, econ_enabled=False):
        return "system"

    async def _fake_context(session, **kwargs):
        return ""

    async def _fake_recent(session, *, channel, limit):
        return []

    async def _fake_chat(cfg, *, system, messages, model, temperature=None):
        return raw

    async def _fake_apply_if_any(session, *, cfg, target_id, text, asker_id=None, intent_kind=None):
        calls.update({
            "target_id": target_id,
            "text": text,
            "asker_id": asker_id,
            "intent_kind": intent_kind,
        })
        return object()

    async def _fake_add_message(session, **kwargs):
        return None

    orig = {
        "heal": drun_service._heal_if_poisoned,
        "get_config": drun_service.drun_config.get_config,
        "system": drun_service.drun_persona.build_system_prompt,
        "context": drun_service.drun_context.build_context,
        "recent": drun_service.drun_memory.recent_messages,
        "chat": drun_service.drun_provider.chat,
        "apply": drun_service.drun_actions.apply_if_any,
        "add": drun_service.drun_memory.add_message,
        "mood": drun_service._peek_mood,
        "emotion": drun_service._peek_emotion,
        "affinity": drun_service._peek_affinity,
        "opinion": drun_service._peek_opinion,
    }

    async def _noop_heal(session):
        return False

    async def _neutral_mood(session, channel):
        return None, 1

    class _Emotion:
        arousal = 0.0
        valence = 0.0

        def directive(self):
            return ""

    async def _neutral_emotion(session):
        return _Emotion()

    async def _zero_affinity(session, subject_id):
        return 0

    async def _neutral_opinion(session, subject_id):
        return {"annoyance": 0, "respect": 0, "entertainment": 0, "trust": 0}

    drun_service._heal_if_poisoned = _noop_heal  # type: ignore[assignment]
    drun_service.drun_config.get_config = _fake_get_config  # type: ignore[assignment]
    drun_service.drun_persona.build_system_prompt = _fake_system  # type: ignore[assignment]
    drun_service.drun_context.build_context = _fake_context  # type: ignore[assignment]
    drun_service.drun_memory.recent_messages = _fake_recent  # type: ignore[assignment]
    drun_service.drun_provider.chat = _fake_chat  # type: ignore[assignment]
    drun_service.drun_actions.apply_if_any = _fake_apply_if_any  # type: ignore[assignment]
    drun_service.drun_memory.add_message = _fake_add_message  # type: ignore[assignment]
    drun_service._peek_mood = _neutral_mood  # type: ignore[assignment]
    drun_service._peek_emotion = _neutral_emotion  # type: ignore[assignment]
    drun_service._peek_affinity = _zero_affinity  # type: ignore[assignment]
    drun_service._peek_opinion = _neutral_opinion  # type: ignore[assignment]
    try:
        yield calls
    finally:
        drun_service._heal_if_poisoned = orig["heal"]  # type: ignore[assignment]
        drun_service.drun_config.get_config = orig["get_config"]  # type: ignore[assignment]
        drun_service.drun_persona.build_system_prompt = orig["system"]  # type: ignore[assignment]
        drun_service.drun_context.build_context = orig["context"]  # type: ignore[assignment]
        drun_service.drun_memory.recent_messages = orig["recent"]  # type: ignore[assignment]
        drun_service.drun_provider.chat = orig["chat"]  # type: ignore[assignment]
        drun_service.drun_actions.apply_if_any = orig["apply"]  # type: ignore[assignment]
        drun_service.drun_memory.add_message = orig["add"]  # type: ignore[assignment]
        drun_service._peek_mood = orig["mood"]  # type: ignore[assignment]
        drun_service._peek_emotion = orig["emotion"]  # type: ignore[assignment]
        drun_service._peek_affinity = orig["affinity"]  # type: ignore[assignment]
        drun_service._peek_opinion = orig["opinion"]  # type: ignore[assignment]


def test_generate_grant_to_subject_is_drun_initiative_not_self_grant():
    with _stubs("На, держи на бутер.\n[[econ:grant:25:по жалости]]") as calls:
        result = _run(drun_service.generate(
            _FakeSession(),
            task="ответь игроку",
            subject_id=123,
            allow_actions=True,
            intent_kind="support",
            vary=False,
        ))

    assert result.ok is True
    assert "[[econ:" not in result.text
    assert calls["target_id"] == 123
    assert calls["asker_id"] is None
    assert calls["intent_kind"] == "support"


def test_generate_still_honors_explicit_self_grant_guard_when_caller_sets_asker():
    with _stubs("Держи.\n[[econ:grant:25:по жалости]]") as calls:
        result = _run(drun_service.generate(
            _FakeSession(),
            task="ответь игроку",
            subject_id=123,
            asker_id=123,
            allow_actions=True,
            intent_kind="support",
            vary=False,
        ))

    assert result.ok is True
    assert calls["target_id"] == 123
    assert calls["asker_id"] == 123
