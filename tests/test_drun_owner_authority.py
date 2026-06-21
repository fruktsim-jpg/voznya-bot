"""Тесты расширенной власти владельца: trusted-резолв кличек + create_event."""

from __future__ import annotations

import asyncio

from app.features.drun import aliases as drun_aliases
from app.features.drun import owner_dm
from app.features.drun import registry as drun_registry


def _run(coro):
    return asyncio.run(coro)


# --- trusted alias resolution ------------------------------------------------


def test_pick_resolved_trusted_allows_weight_one():
    # Автономный резолв (не trusted) требует устойчивости (вес ≥3) → None.
    assert drun_aliases._pick_resolved({101: 1}) is None
    # Явная команда владельца (trusted) принимает даже вес 1.
    assert drun_aliases._pick_resolved({101: 1}, trusted=True) == 101


def test_pick_resolved_trusted_still_blocks_ambiguous_collision():
    # Даже владельцу не угадываем при близкой коллизии тёзок (margin).
    assert drun_aliases._pick_resolved({101: 1, 202: 1}, trusted=True) is None
    # Явный перевес лидера — резолвим.
    assert drun_aliases._pick_resolved({101: 3, 202: 1}, trusted=True) == 101


def test_pick_resolved_untrusted_unchanged():
    assert drun_aliases._pick_resolved({101: 3}) == 101
    assert drun_aliases._pick_resolved({101: 2}) is None


# --- create_event tool -------------------------------------------------------


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        # Эмулируем autoincrement: присваиваем id при flush, как настоящая БД.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                try:
                    obj.id = 1
                except Exception:
                    pass

    async def execute(self, *_a, **_k):
        class _R:
            def all(self_inner):
                return []

        return _R()


def _ctx(args: dict) -> drun_registry.ToolContext:
    async def _who(_w):
        return None

    async def _aud(**_k):
        return []

    return drun_registry.ToolContext(
        session=_FakeSession(), owner_id=1, args=args,
        resolve_who=_who, resolve_audience=_aud,
    )


def test_create_event_registered_in_registry():
    assert "create_event" in drun_registry.REGISTRY
    assert "ивент" in drun_registry.all_hints()


def test_create_event_requires_title():
    res = _run(drun_registry.dispatch(_ctx({"kind": "mini"}), "create_event"))
    assert res is not None
    assert res.ok is False


def test_create_event_clamps_reward_and_creates():
    res = _run(drun_registry.dispatch(
        _ctx({"kind": "челлендж", "title": "Первый до 5 побед", "reward": 999999}),
        "create_event",
    ))
    assert res is not None
    assert res.ok is True
    assert res.meta.get("event_id")
    assert res.meta.get("kind") == "challenge"


# --- owner DM diagnostics ----------------------------------------------------


def test_owner_diag_parser_status_commands():
    assert owner_dm._parse_owner_diag("друн архив статус") == ("archive_status", "")
    assert owner_dm._parse_owner_diag("drun memory status") == ("memory_status", "")
    assert owner_dm._parse_owner_diag("джобы статус") == ("jobs_status", "")
    assert owner_dm._parse_owner_diag("друн критик статус") == ("critic_status", "")


def test_owner_diag_parser_search_commands():
    assert owner_dm._parse_owner_diag("друн архив поиск pgvector") == (
        "archive_search", "pgvector",
    )
    assert owner_dm._parse_owner_diag("память поиск хинт") == (
        "memory_search", "хинт",
    )
    assert owner_dm._parse_owner_diag("друн человек найти хинт") == (
        "person_find", "хинт",
    )


def test_owner_diag_parser_ignores_regular_talk():
    assert owner_dm._parse_owner_diag("друн как дела") is None
