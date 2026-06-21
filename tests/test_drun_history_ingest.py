"""Pure-тесты исторической памяти из Combot."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.features.drun.history_ingest import (
    KIND_PLAYER_ALIAS,
    KIND_PLAYER_HISTORY,
    SOURCE,
    build_player_proposals,
)


def _combot_user(**overrides):
    data = {
        "user_id": 123,
        "username": "old_user",
        "title": "Старый Ник",
        "joined_at": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "days_since_joined": 900,
        "messages": 1200,
        "xp": 3456,
        "rep": 12,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_build_player_proposals_skips_quiet_users():
    row = _combot_user(messages=10)
    assert build_player_proposals(row, min_messages=100) == []


def test_build_player_proposals_creates_history_fact():
    row = _combot_user()
    proposals = build_player_proposals(row, min_messages=100)
    history = [p for p in proposals if p.kind == KIND_PLAYER_HISTORY][0]
    assert history.subject_id == 123
    assert history.source == SOURCE
    assert history.weight == 3
    assert "1200 сообщений" in history.fact
    assert "репутация Combot: 12" in history.fact


def test_build_player_proposals_keeps_historical_aliases():
    row = _combot_user(title="Кот", username="kot_old")
    live = SimpleNamespace(full_name="Новый Ник", username="new_user")
    proposals = build_player_proposals(row, live_user=live, min_messages=100)
    aliases = [p for p in proposals if p.kind == KIND_PLAYER_ALIAS]
    assert {p.ttl_days for p in aliases} == {180}
    assert any("Кот" in p.fact for p in aliases)
    assert any("kot_old" in p.fact for p in aliases)


def test_build_player_proposals_does_not_duplicate_live_name_alias():
    row = _combot_user(title="Новый Ник", username="new_user")
    live = SimpleNamespace(full_name="Новый Ник", username="new_user")
    proposals = build_player_proposals(row, live_user=live, min_messages=100)
    assert all(p.kind != KIND_PLAYER_ALIAS for p in proposals)
