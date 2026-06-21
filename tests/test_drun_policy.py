"""Pure-тесты поведенческой политики Друна."""

from __future__ import annotations

from app.features.drun.policy import build_policy_from_signals


def test_policy_blocks_econ_on_recent_econ_cooldown():
    policy = build_policy_from_signals(recent_econ_remaining=120, intent_kind="hype")
    assert policy.allow_econ_hint is False
    assert "НЕ обещай" in policy.block()


def test_policy_new_user_avoids_old_lore_pressure():
    policy = build_policy_from_signals(messages_count=3)
    assert policy.allow_econ_hint is True
    assert "почти новый" in policy.block()


def test_policy_hot_chat_demands_shorter_reply():
    policy = build_policy_from_signals(messages_count=100, chat_heat=10)
    assert "короче" in policy.block()


def test_policy_direct_question_prioritizes_answer():
    policy = build_policy_from_signals(messages_count=100, addressed=True, has_question=True)
    assert "сначала ответь" in policy.block()


def test_policy_positive_affinity_treats_as_own():
    policy = build_policy_from_signals(messages_count=100, affinity_score=50)
    assert "скорее свой" in policy.block()
