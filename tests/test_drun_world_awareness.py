"""Тесты расширения world-awareness друна (Пункт 3).

Чистая логика без БД/LLM: каталог событий, маппинг модерации в события,
классификация настроения, конфиг автономности.
"""

from __future__ import annotations

from app.features.drun import config as drun_config
from app.features.drun import mood as drun_mood
from app.repositories import moderation as mod_repo
from app.services import world_events as we


def test_moderation_event_constants_have_severity():
    # Новые карательные типы должны быть в каталоге severity (иначе emit упадёт
    # на DEFAULT_SEVERITY.get → 0 и потеряет NOTIFY-приоритет).
    for t in (we.EVENT_MOD_BAN, we.EVENT_MOD_MUTE, we.EVENT_MOD_WARN, we.EVENT_MOD_KICK):
        assert t in we.DEFAULT_SEVERITY
    # Бан/кик — заметные (severity ≥ 2, попадают в NOTIFY), варн/мьют — тише.
    assert we.DEFAULT_SEVERITY[we.EVENT_MOD_BAN] >= 2
    assert we.DEFAULT_SEVERITY[we.EVENT_MOD_KICK] >= 2


def test_mod_action_map_covers_bot_and_drun_actions():
    m = mod_repo._MOD_ACTION_EVENT
    # Команды модераторов (player.*) и собственные инструменты друна (owner_*)
    # должны мапиться на один и тот же тип события — единый чокпоинт.
    assert m["player.ban"] == we.EVENT_MOD_BAN
    assert m["owner_ban"] == we.EVENT_MOD_BAN
    assert m["player.mute"] == we.EVENT_MOD_MUTE
    assert m["owner_mute"] == we.EVENT_MOD_MUTE
    assert m["player.warn"] == we.EVENT_MOD_WARN
    assert m["owner_warn"] == we.EVENT_MOD_WARN
    assert m["player.kick"] == we.EVENT_MOD_KICK
    assert m["owner_kick"] == we.EVENT_MOD_KICK


def test_mod_action_map_excludes_positive_actions():
    # Снятия и невраждебные owner-действия НЕ должны порождать событие репрессии.
    m = mod_repo._MOD_ACTION_EVENT
    for action in (
        "player.unban", "owner_unban", "player.unmute", "owner_unmute",
        "owner_unwarn", "owner_grant_one", "owner_set_setting",
        "owner_award_mmr", "owner_grant_item",
    ):
        assert action not in m


def test_mood_classifies_moderation_as_conflict():
    # Репрессии добавляют напряжения → конфликтные типы (хаос/злость).
    assert we.EVENT_MOD_BAN in drun_mood._CONFLICT_TYPES
    assert we.EVENT_MOD_MUTE in drun_mood._CONFLICT_TYPES
    assert we.EVENT_MOD_KICK in drun_mood._CONFLICT_TYPES


def test_mood_classifies_achievement_and_gift_as_celebratory():
    # Раньше ачивки/обычная выдача подарка были вне классификации настроения.
    assert we.EVENT_ACHIEVEMENT_UNLOCKED in drun_mood._CELEBRATORY_TYPES
    assert we.EVENT_GIFT_DELIVERED in drun_mood._CELEBRATORY_TYPES


def test_autonomous_min_gap_default_conservative():
    # Анти-спам по умолчанию: ощутимая пауза между автопостами.
    assert drun_config.DEFAULTS[drun_config.KEY_AUTONOMOUS_MIN_GAP] >= 30
    # Автономность по-прежнему выключена по умолчанию (явный опт-ин).
    assert drun_config.DEFAULTS[drun_config.KEY_AUTONOMOUS_ENABLED] is False

