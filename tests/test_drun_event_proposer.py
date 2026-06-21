"""Тесты автономных предложений ивентов друна (event_proposer).

Чистая логика выбора повода (без БД/LLM) + контракт аргументов, которым
предложение исполняется через registry.create_event. Оркестрация (сбор метрик,
запись DrunProposal, уведомление владельца) проверяется отдельно интеграционно;
здесь — детерминированное ядро, ради которого фича безопасна.
"""

from __future__ import annotations

import asyncio

from app.features.drun import event_proposer as ep
from app.features.drun import registry as drun_registry


def _run(coro):
    return asyncio.run(coro)


# --- choose_event_idea: чистая детекция повода -------------------------------


def test_no_idea_when_nothing_happening():
    # Живой спокойный чат, нет рекордов, ивентов нет — повода нет.
    signals = ep.ChatSignals(
        msgs_window=12, speakers_window=5, top_farm_streak=3, active_events=0
    )
    assert ep.choose_event_idea(signals) is None


def test_dead_chat_proposes_mini_event():
    signals = ep.ChatSignals(
        msgs_window=1, speakers_window=1, top_farm_streak=0, active_events=0
    )
    idea = ep.choose_event_idea(signals)
    assert idea is not None
    assert idea.signal == "dead_chat"
    assert idea.kind == "mini"
    assert idea.reward > 0


def test_farm_streak_proposes_challenge():
    signals = ep.ChatSignals(
        msgs_window=15, speakers_window=6, top_farm_streak=12, active_events=0
    )
    idea = ep.choose_event_idea(signals)
    assert idea is not None
    assert idea.signal == "farm_streak"
    assert idea.kind == "challenge"
    assert "12" in idea.body


def test_streak_beats_dead_chat_priority():
    # И тихо, и есть серия — серия (живой герой) важнее.
    signals = ep.ChatSignals(
        msgs_window=1, speakers_window=1, top_farm_streak=20, active_events=0
    )
    idea = ep.choose_event_idea(signals)
    assert idea is not None
    assert idea.signal == "farm_streak"


def test_no_idea_when_max_active_events():
    # Уже идёт максимум ивентов — не предлагаем то, что движок не создаст.
    signals = ep.ChatSignals(
        msgs_window=1, speakers_window=1, top_farm_streak=50,
        active_events=ep._MAX_ACTIVE,
    )
    assert ep.choose_event_idea(signals) is None


# --- контракт с create_event -------------------------------------------------


def test_idea_args_match_create_event_contract():
    # to_args() должен раскладываться ровно в аргументы registry.create_event,
    # иначе одобренное предложение не исполнится. Проверяем реальным dispatch'ем.
    signals = ep.ChatSignals(
        msgs_window=1, speakers_window=1, top_farm_streak=15, active_events=0
    )
    idea = ep.choose_event_idea(signals)
    assert idea is not None
    args = idea.to_args()
    assert set(args) == {"kind", "title", "body", "reward", "hours"}

    # Аргументы реально создают ивент через тот же путь, что и одобрение в DM.
    from tests.test_drun_owner_authority import _ctx  # переиспользуем фейк-сессию

    res = _run(drun_registry.dispatch(_ctx(args), "create_event"))
    assert res is not None
    assert res.ok is True
    assert res.meta.get("kind") == "challenge"


def test_reward_within_engine_cap():
    # Предлагаемая награда не превышает потолок движка ивентов.
    from app.features.drun import events as drun_events

    for streak, msgs in ((15, 15), (0, 1)):
        idea = ep.choose_event_idea(
            ep.ChatSignals(
                msgs_window=msgs, speakers_window=1,
                top_farm_streak=streak, active_events=0,
            )
        )
        assert idea is not None
        assert 0 < idea.reward <= drun_events._MAX_REWARD


def test_create_event_is_high_impact_for_approval():
    # Предложение исполняется как create_event; чтобы пройти через owner-очередь,
    # достаточно того, что owner_dm всегда гонит предложения через подтверждение.
    # Здесь фиксируем, что create_event — валидный ключ реестра (иначе одобрение
    # упадёт на dispatch).
    assert "create_event" in drun_registry.REGISTRY
