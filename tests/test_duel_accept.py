"""Тесты принятия дуэлей."""

from __future__ import annotations

import asyncio

from app.features.duel.service import accept_challenge, create_challenge
from app.models import PendingAction, User


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


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
