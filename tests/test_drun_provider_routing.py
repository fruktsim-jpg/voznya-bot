"""Тесты роутинга формата провайдера (OpenAI-совместимый vs Anthropic)."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.features.drun import provider
from app.features.drun.config import AiConfig


def _run(coro):
    return asyncio.run(coro)


def test_wellflow_gateway_uses_openai_format():
    # Шлюз wellflow отдаёт claude через /v1/chat/completions — НЕ Anthropic.
    assert not provider._is_anthropic(
        "https://api.wellflow.dev/v1", "claude-opus-4.8"
    )
    assert not provider._is_anthropic(
        "https://api.wellflow.dev/v1", "gpt-5.5"
    )


def test_real_anthropic_uses_messages_format():
    assert provider._is_anthropic("https://api.anthropic.com/v1", "claude-opus-4.8")


def test_generic_openai_gateway_uses_openai_format():
    assert not provider._is_anthropic("https://vibecode.moe/v1", "claude-opus-4-8")
    assert not provider._is_anthropic("https://api.openai.com/v1", "gpt-5.5")


def test_wants_messages_endpoint_detection():
    # Точный текст ошибки vibecode для claude-* на /chat/completions.
    assert provider._wants_messages_endpoint(
        "this model is only available via /v1/messages (Anthropic format)"
    )
    assert provider._wants_messages_endpoint("Use Anthropic format instead")
    # Прочие 400 не должны триггерить ретрай.
    assert not provider._wants_messages_endpoint("unknown model claude-opus-4.8")
    assert not provider._wants_messages_endpoint("")


def test_normalize_claude_model_dots_to_dashes():
    # vibecode/Anthropic ждут дефисы в версии claude-модели.
    assert provider._normalize_claude_model("claude-opus-4.8") == "claude-opus-4-8"
    assert provider._normalize_claude_model("claude-haiku-4.5") == "claude-haiku-4-5"
    # Уже корректный id не меняется.
    assert provider._normalize_claude_model("claude-opus-4-8") == "claude-opus-4-8"
    # Не-claude модели не трогаем (точки в версии GPT/Gemini значимы).
    assert provider._normalize_claude_model("gpt-5.5") == "gpt-5.5"
    assert provider._normalize_claude_model("gemini-3.1-pro-preview") == (
        "gemini-3.1-pro-preview"
    )


def _cfg(**over) -> AiConfig:
    base = dict(
        enabled=True, base_url="https://vibecode.moe/v1", api_key="k",
        model="claude-opus-4.8", fast_model="", models_by_role={}, temperature=1.0,
        max_tokens=600, posts_per_day_max=6, min_severity=2,
        autonomous_enabled=False, reply_enabled=True, reply_cooldown_sec=20,
        name_triggers=["друн"], random_butt_in_chance=0.0, econ_enabled=False,
        econ_max_pct=0.05, econ_max_abs=1000, econ_cooldown_sec=7200,
        econ_daily_cap=20,
    )
    base.update(over)
    return AiConfig(**base)


class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    """Минимальная замена aiohttp.ClientSession: пишет вызовы в ``calls``."""

    def __init__(self, routes):
        # routes: dict url -> (status, body)
        self._routes = routes
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None):
        self.calls.append((url, json or {}))
        status, body = self._routes[url]
        return _FakeResp(status, body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _install_fake_aiohttp(monkeypatch, session):
    fake = types.ModuleType("aiohttp")

    class _Timeout:
        def __init__(self, total=None):
            self.total = total

    class _ClientError(Exception):
        pass

    fake.ClientTimeout = _Timeout
    fake.ClientError = _ClientError
    fake.ClientSession = lambda *a, **k: session
    monkeypatch.setitem(sys.modules, "aiohttp", fake)


def test_chat_retries_on_messages_endpoint(monkeypatch):
    base = "https://vibecode.moe/v1"
    session = _FakeSession({
        f"{base}/chat/completions": (
            400,
            '{"error":{"message":"this model is only available via /v1/messages '
            '(Anthropic format)","type":"invalid_request_error"}}',
        ),
        f"{base}/messages": (
            200,
            '{"content":[{"type":"text","text":"pong"}],"role":"assistant"}',
        ),
    })
    _install_fake_aiohttp(monkeypatch, session)

    out = _run(provider.chat(
        _cfg(), system="s", messages=[{"role": "user", "content": "hi"}],
    ))
    assert out == "pong"
    # Должно быть два вызова: сначала OpenAI-формат, затем /messages.
    assert [c[0] for c in session.calls] == [
        f"{base}/chat/completions", f"{base}/messages",
    ]
    # На /messages id модели нормализован под дефисы.
    assert session.calls[1][1]["model"] == "claude-opus-4-8"


def test_chat_other_400_does_not_retry(monkeypatch):
    base = "https://vibecode.moe/v1"
    session = _FakeSession({
        f"{base}/chat/completions": (
            400, '{"error":{"message":"unknown model foo"}}',
        ),
    })
    _install_fake_aiohttp(monkeypatch, session)

    with pytest.raises(provider.LlmError):
        _run(provider.chat(
            _cfg(model="foo"), system="s",
            messages=[{"role": "user", "content": "hi"}],
        ))
    # Никакого ретрая на /messages.
    assert [c[0] for c in session.calls] == [f"{base}/chat/completions"]
