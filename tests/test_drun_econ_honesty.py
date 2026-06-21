"""Детерминированная защита от брехни про ешки."""

from __future__ import annotations

from app.features.drun.econ import EconResult
from app.features.drun.service import _guard_econ_claim, _money_claim_kind


def test_money_claim_detects_grant_words():
    assert _money_claim_kind("на, держи 20 ешек на бутер") == "grant"
    assert _money_claim_kind("я тебе накинул ешек") == "grant"


def test_money_claim_detects_tax_words():
    assert _money_claim_kind("плати налог, клоун") == "tax"
    assert _money_claim_kind("снял с тебя ешки за понты") == "tax"


def test_guard_leaves_successful_matching_grant():
    text = "держи 20 ешек, бедолага"
    out = _guard_econ_claim(text, EconResult(ok=True, kind="grant", applied=20))
    assert out == text


def test_guard_adds_correction_without_directive():
    out = _guard_econ_claim("накину тебе ешек", None)
    assert "реально не трогал" in out


def test_guard_adds_block_reason_when_econ_failed():
    out = _guard_econ_claim(
        "держи ешки",
        EconResult(ok=False, kind="grant", reason="cooldown"),
    )
    assert "система не дала (cooldown)" in out
