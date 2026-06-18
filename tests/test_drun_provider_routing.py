"""Тесты роутинга формата провайдера (OpenAI-совместимый vs Anthropic)."""

from __future__ import annotations

from app.features.drun import provider


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
