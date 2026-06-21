from __future__ import annotations

from app.features.drun.context import ContextIntent, classify_context_route


def test_context_router_past_uses_archive_and_skips_economy():
    route = classify_context_route("помнишь когда хинт писал про pgvector?")

    assert route.intent == ContextIntent.PAST
    assert route.include_archive is True
    assert route.archive_limit == 8
    assert route.include_economy is False
    assert route.include_web is False


def test_context_router_web_uses_web_and_suppresses_background_noise():
    route = classify_context_route("найди что такое pgvector сейчас")

    assert route.intent == ContextIntent.WEB
    assert route.include_web is True
    assert route.include_overview is False
    assert route.include_worldview is False
    assert route.include_economy is False


def test_context_router_economy_keeps_money_blocks():
    route = classify_context_route("у кого больше ешек и кто богатый")

    assert route.intent == ContextIntent.ECONOMY
    assert route.include_economy is True
    assert route.include_archive is False
    assert route.include_worldview is False


def test_context_router_person_focuses_social_memory():
    route = classify_context_route("расскажи про хинта", subject_id=123)

    assert route.intent == ContextIntent.PERSON
    assert route.include_memory is True
    assert route.include_worldview is True
    assert route.include_economy is False


def test_context_router_owner_dm_is_light_and_deterministic():
    route = classify_context_route("друн память статус", channel="owner_dm")

    assert route.intent == ContextIntent.OWNER
    assert route.include_recent_chat is False
    assert route.include_archive is False
    assert route.include_web is False


def test_context_router_default_keeps_background_but_no_archive():
    route = classify_context_route("ну и что дальше")

    assert route.intent == ContextIntent.DEFAULT
    assert route.include_memory is True
    assert route.include_archive is False
    assert route.include_economy is True
