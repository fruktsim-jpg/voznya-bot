"""Тесты принятия дуэлей."""

from __future__ import annotations

import asyncio

from app.features.duel.service import accept_challenge, create_challenge
from app.core.utils import now_utc
from app.models import PendingAction, User


def _run(coro):
    # asyncio.run создаёт и закрывает свежий event loop. get_event_loop() без
    # запущенного цикла бросает RuntimeError на Python 3.10+ (жёстко в 3.14).
    return asyncio.run(coro)


async def _make_session():
    return _FakeSession()


class _FakeSession:
    def __init__(self):
        self.users: dict[int, User] = {}
        self.pending: dict[int, PendingAction] = {}
        self._next_pending_id = 1

    def add(self, obj):
        if isinstance(obj, User):
            self.users[obj.user_id] = obj
        elif isinstance(obj, PendingAction):
            obj.id = self._next_pending_id
            self._next_pending_id += 1
            self.pending[obj.id] = obj

    def add_all(self, objects):
        for obj in objects:
            self.add(obj)

    async def flush(self):
        return None

    async def get(self, model, key, **_kwargs):
        if model is User:
            return self.users.get(key)
        if model is PendingAction:
            return self.pending.get(key)
        return None

    async def execute(self, *_args, **_kwargs):
        pending = next(
            (
                item for item in self.pending.values()
                if item.action_type == "duel"
                and item.status == "pending"
                and item.expires_at > now_utc()
            ),
            None,
        )
        return _FakeExecuteResult(pending)


class _FakeExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


def test_duel_cooldown_set_only_for_initiator(monkeypatch):
    async def scenario():
        session = await _make_session()
        session.add_all(
            [
                User(user_id=1, first_name="init", balance=100),
                User(user_id=2, first_name="target", balance=100),
            ]
        )
        for user in session.users.values():
            user.duels_won = 0
            user.duels_lost = 0
            user.duel_loss_streak = 0
        await session.flush()

        cooldowns: list[tuple[int, str, int]] = []

        async def fake_set_cooldown(_session, user_id, action, seconds):
            cooldowns.append((user_id, action, seconds))

        async def fake_participation_mmr(*_args, **_kwargs):
            return 0

        async def fake_award_mmr(*_args, **_kwargs):
            return None

        async def fake_get_mmr(*_args, **_kwargs):
            return 0

        monkeypatch.setattr("app.features.duel.service.cooldowns.set_cooldown", fake_set_cooldown)
        monkeypatch.setattr("app.features.duel.service.dynamic.get_int", _fake_dynamic_get_int)
        monkeypatch.setattr("app.features.duel.service._participation_mmr", fake_participation_mmr)
        monkeypatch.setattr("app.features.mmr.service.award_mmr", fake_award_mmr)
        monkeypatch.setattr("app.repositories.mmr.get_mmr", fake_get_mmr)
        monkeypatch.setattr("app.features.season.service.progress_mission", _fake_progress_mission)
        monkeypatch.setattr("app.services.world_events.emit_safe", _fake_emit_safe)

        challenge = await create_challenge(session, 1, 2, 25, -100)
        result = await accept_challenge(session, 2, pending_id=challenge.pending_id)

        assert result.status == "done"
        assert cooldowns == [(1, "duel", 60)]

    _run(scenario())


def test_initiator_cannot_create_second_pending_duel():
    async def scenario():
        session = await _make_session()
        session.add(User(user_id=1, first_name="init", balance=100))
        await session.flush()

        first = await create_challenge(session, 1, None, 25, -100)
        second = await create_challenge(session, 1, None, 25, -100)

        assert first.status == "ok"
        assert second.status == "pending_exists"

    _run(scenario())


def test_open_duel_stays_pending_when_poor_user_clicks_accept():
    async def scenario():
        session = await _make_session()
        session.add_all(
            [
                User(user_id=1, first_name="init", balance=100),
                User(user_id=2, first_name="poor", balance=10),
            ]
        )
        await session.flush()

        challenge = await create_challenge(session, 1, None, 25, -100)
        poor_result = await accept_challenge(session, 2, pending_id=challenge.pending_id)

        pending = await session.get(PendingAction, challenge.pending_id)
        assert poor_result.status == "target_poor"
        assert pending is not None
        assert pending.status == "pending"

    _run(scenario())


async def _fake_dynamic_get_int(_session, _key, _default):
    return 60


async def _fake_progress_mission(*_args, **_kwargs):
    return None


async def _fake_emit_safe(*_args, **_kwargs):
    return None
