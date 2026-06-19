"""Тесты DrunPresence — surface-agnostic слой вывода друна (Phase 3).

Проверяем маршрутизацию по поверхностям и грейсфул-деградацию (Presence НИКОГДА
не бросает), через фейковый Bot и фейковый sessionmaker. SQL/Telegram настоящие
не нужны — нас интересует логика выбора поверхности и устойчивость к сбоям.
"""

from __future__ import annotations

import asyncio

from app.features.drun.presence import (
    DrunPresence,
    PresenceTarget,
    Surface,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeMessage:
    def __init__(self, message_id: int = 1) -> None:
        self.message_id = message_id


class _FakeBot:
    """Записывает вызовы send_message; можно заставить падать."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self._fail = fail

    async def send_message(self, chat_id, text, *, parse_mode=None, reply_to_message_id=None):
        if self._fail:
            raise RuntimeError("boom")
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return _FakeMessage(message_id=len(self.calls))


def test_say_group_uses_group_chat_id():
    bot = _FakeBot()
    p = DrunPresence(bot=bot, group_chat_id=-100500)
    res = _run(p.say_group("привет чат"))
    assert res.ok
    assert res.surface is Surface.GROUP
    assert bot.calls[0]["chat_id"] == -100500
    assert bot.calls[0]["text"] == "привет чат"


def test_say_dm_targets_user_private_chat():
    bot = _FakeBot()
    p = DrunPresence(bot=bot, group_chat_id=-100500)
    res = _run(p.say_dm(42, "только тебе, владелец"))
    assert res.ok
    assert res.surface is Surface.DM
    # В Telegram приватный chat_id == user_id.
    assert bot.calls[0]["chat_id"] == 42


def test_group_target_override_chat_id():
    bot = _FakeBot()
    p = DrunPresence(bot=bot, group_chat_id=-100500)
    res = _run(p.deliver(PresenceTarget(surface=Surface.GROUP, chat_id=-999), "x"))
    assert res.ok
    assert bot.calls[0]["chat_id"] == -999


def test_empty_text_is_rejected_not_sent():
    bot = _FakeBot()
    p = DrunPresence(bot=bot, group_chat_id=-100500)
    res = _run(p.say_group("   "))
    assert not res.ok
    assert res.error == "empty"
    assert bot.calls == []


def test_telegram_failure_degrades_gracefully():
    # Сбой доставки НЕ должен ронять вызывающего — Presence возвращает ok=False.
    bot = _FakeBot(fail=True)
    p = DrunPresence(bot=bot, group_chat_id=-100500)
    res = _run(p.say_group("упадёт"))
    assert not res.ok
    assert res.surface is Surface.GROUP


def test_web_without_sessionmaker_degrades():
    # Веб-поверхность требует sessionmaker для персиста; без него — мягкий отказ.
    bot = _FakeBot()
    p = DrunPresence(bot=bot, group_chat_id=-100500)
    res = _run(p.deliver(PresenceTarget.web(user_id=7), "в ленту"))
    assert not res.ok
    assert res.surface is Surface.WEB
    assert res.error == "no_sessionmaker"


def test_target_constructors():
    assert PresenceTarget.group(-1).surface is Surface.GROUP
    dm = PresenceTarget.dm(5)
    assert dm.surface is Surface.DM and dm.chat_id == 5 and dm.user_id == 5
    web = PresenceTarget.web()
    assert web.surface is Surface.WEB and web.chat_id is None
