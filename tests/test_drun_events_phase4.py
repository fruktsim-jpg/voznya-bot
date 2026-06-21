"""Тесты owner-intelligence и автономных ивентов друна (Phase 4 + Phase 6).

Чистая логика + ин-мемори фейк-сессия для жизненного цикла. Без реальной БД и
LLM: проверяем классификацию high-impact, парсер решений/предпочтений в
owner_dm, кламп награды и переходы статусов ивента.
"""

from __future__ import annotations

from app.features.drun import events as drun_events
from app.features.drun import owner as drun_owner
from app.features.drun import owner_dm


# --- Phase 6: owner-intelligence (чистое) ------------------------------------


def test_high_impact_classification():
    # Массовые/необратимые действия требуют подтверждения.
    assert drun_owner.is_high_impact("grant")
    assert drun_owner.is_high_impact("ban")
    assert drun_owner.is_high_impact("multiplier")
    # Правка параметров мира / вкл-выкл подсистем — тоже high-impact.
    assert drun_owner.is_high_impact("set_param")
    assert drun_owner.is_high_impact("feature_toggle")
    # Малые/точечные — нет.
    assert not drun_owner.is_high_impact("grant_one")
    assert not drun_owner.is_high_impact("mute")
    assert not drun_owner.is_high_impact("warn")


def test_high_impact_tools_exist_in_registry():
    # Регресс-гард против рассинхрона: каждое имя из HIGH_IMPACT_TOOLS обязано
    # быть реальным ключом реестра. Раньше тут жил «set_setting», которого в
    # registry нет (реальные тулы — set_param/feature_toggle), и правка мира
    # молча шла мимо approval-flow в личке владельца.
    from app.features.drun import registry as drun_registry

    for tool in drun_owner.HIGH_IMPACT_TOOLS:
        assert tool in drun_registry.REGISTRY, tool


def test_decision_parser():
    assert owner_dm._parse_decision("да") == (True, None)
    assert owner_dm._parse_decision("да 5") == (True, 5)
    assert owner_dm._parse_decision("нет 7") == (False, 7)
    assert owner_dm._parse_decision("одобряю") == (True, None)
    assert owner_dm._parse_decision("отклоняю 3") == (False, 3)
    # Не-решения.
    assert owner_dm._parse_decision("дай всем по 100") is None
    assert owner_dm._parse_decision("как дела") is None


def test_decision_parser_uses_keyword_tuples():
    # Слова решения берутся из _APPROVE/_REJECT — не молчат на «ок/давай/отмена».
    for w in owner_dm._APPROVE:
        assert owner_dm._parse_decision(w) == (True, None), w
    for w in owner_dm._REJECT:
        assert owner_dm._parse_decision(w) == (False, None), w
    # Многословные фразы из набора + хвостовой номер.
    assert owner_dm._parse_decision("не надо 4") == (False, 4)
    assert owner_dm._parse_decision("подтверждаю 9") == (True, 9)


def test_preference_extraction():
    assert owner_dm._extract_preference("запомни: новичков не баню") == "новичков не баню"
    assert owner_dm._extract_preference("правило: инфляцию гаси налогом") == "инфляцию гаси налогом"
    assert owner_dm._extract_preference("просто болтовня") is None


def test_describe_call_readable():
    assert owner_dm._describe_call("ban", {"who": "кот"}) == "ban(who=кот)"
    assert owner_dm._describe_call("spawn_treasure", {}) == "spawn_treasure"


# --- Phase 4: events (чистое) ------------------------------------------------


def test_reward_clamp():
    assert drun_events._clamp_reward(None) == 0
    assert drun_events._clamp_reward(0) == 0
    assert drun_events._clamp_reward(-50) == 0
    assert drun_events._clamp_reward(100) == 100
    # Жёсткий потолок — друн не печатает экономику.
    assert drun_events._clamp_reward(10_000_000) == drun_events._MAX_REWARD


def test_event_kind_aliases_in_handlers():
    from app.features.drun import events_handlers as eh

    assert eh._KIND_ALIASES["челлендж"] == drun_events.KIND_CHALLENGE
    assert eh._KIND_ALIASES["прогноз"] == drun_events.KIND_PREDICTION
    assert eh._KIND_ALIASES["цель"] == drun_events.KIND_GOAL


# --- Phase 4: события мира ---------------------------------------------------


def test_drun_event_resolved_event_type_registered():
    from app.services import world_events as we

    assert we.EVENT_DRUN_EVENT_RESOLVED in we.DEFAULT_SEVERITY
    # Исход ивента с выплатой — заметное событие (NOTIFY).
    assert we.DEFAULT_SEVERITY[we.EVENT_DRUN_EVENT_RESOLVED] >= 2
