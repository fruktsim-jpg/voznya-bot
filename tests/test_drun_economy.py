"""Тесты чистых помощников экономического чутья друна (economy)."""

from __future__ import annotations

from app.features.drun import economy


def test_reason_ru_known():
    assert economy._ru("casino") == "казино"
    assert economy._ru("farm") == "ферма"
    assert economy._ru("duel") == "дуэли"


def test_reason_ru_unknown_passthrough():
    assert economy._ru("totally_new_reason") == "totally_new_reason"


def test_chat_economy_net():
    ce = economy.ChatEconomy(minted=1000, burned=400)
    assert ce.net == 600


def test_faucet_override_set():
    # Эмиссия сверху (админ/друн) должна быть отделена от честной игры.
    assert "admin" in economy._FAUCET_OVERRIDE
    assert "owner_drun" in economy._FAUCET_OVERRIDE
    assert "farm" not in economy._FAUCET_OVERRIDE
