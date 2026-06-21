"""Static audit helpers for Drun prompt files."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_response_mode_has_global_dialogue_quality_rules():
    text = (ROOT / "app/features/drun/response_mode.py").read_text(encoding="utf-8")

    assert "Глобально для этого ответа" in text
    assert "сначала полезная суть" in text
    assert "Не своди всё к ешкам" in text


def test_persona_mentions_mode_over_template_behavior():
    text = (ROOT / "app/features/drun/persona.py").read_text(encoding="utf-8")

    assert "РЕЖИМЫ ОБЩЕНИЯ" in text
    assert "Ты НЕ обязан быть токсичным всегда" in text
    assert "Если человек задаёт реальный вопрос" in text or "реальный вопрос" in text


def test_service_injects_response_mode_after_variance():
    text = (ROOT / "app/features/drun/service.py").read_text(encoding="utf-8")

    assert "drun_response_mode.mode_directive" in text
    assert "РЕЖИМ ОТВЕТА" in text
