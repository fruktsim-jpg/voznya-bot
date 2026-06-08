"""Тесты чистой логики сезонной системы (без БД).

Проверяем детерминированные функции: дивизионы по season MMR, ежедневная
награда по дню серии, сопоставление условий сезонных титулов, границы недели.
"""

from __future__ import annotations

from datetime import date

from app.features.season.service import _title_matches, week_start
from app.settings import season as cfg


# --- Дивизионы --------------------------------------------------------------


def test_division_bronze_at_zero():
    assert cfg.get_division(0).name == "Bronze"


def test_division_boundaries_inclusive():
    # Ровно на пороге — уже следующий дивизион.
    assert cfg.get_division(500).name == "Silver"
    assert cfg.get_division(1500).name == "Gold"
    assert cfg.get_division(12000).name == "Master"


def test_division_below_threshold_stays_lower():
    assert cfg.get_division(499).name == "Bronze"
    assert cfg.get_division(11999).name == "Diamond"


def test_divisions_sorted_ascending():
    mins = [d.min_mmr for d in cfg.DIVISIONS]
    assert mins == sorted(mins)


# --- Daily reward -----------------------------------------------------------


def test_daily_reward_cycles_every_7_days():
    # День 1 и день 8 дают одинаковую награду (цикл длиной 7).
    assert cfg.daily_reward_for_streak(1) == cfg.daily_reward_for_streak(8)
    assert cfg.daily_reward_for_streak(7) == cfg.DAILY_REWARDS[-1]


def test_daily_reward_grows_within_cycle():
    rewards = [cfg.daily_reward_for_streak(d) for d in range(1, 8)]
    assert rewards == list(cfg.DAILY_REWARDS)


def test_daily_reward_handles_zero_and_negative():
    assert cfg.daily_reward_for_streak(0) == cfg.DAILY_REWARDS[0]
    assert cfg.daily_reward_for_streak(-5) == cfg.DAILY_REWARDS[0]


# --- Сезонные титулы --------------------------------------------------------


def test_title_rank_condition():
    assert _title_matches("rank:1", rank=1, division="Master") is True
    assert _title_matches("rank:3", rank=2, division="Bronze") is True
    assert _title_matches("rank:3", rank=4, division="Bronze") is False


def test_title_division_condition():
    assert _title_matches("division:Master", rank=99, division="Master") is True
    assert _title_matches("division:Master", rank=1, division="Diamond") is False


def test_title_unknown_condition_is_false():
    assert _title_matches("garbage", rank=1, division="Master") is False


# --- Границы недели ---------------------------------------------------------


def test_week_start_is_monday():
    # 2026-06-08 — понедельник; среда той же недели → тот же понедельник.
    monday = date(2026, 6, 8)
    wednesday = date(2026, 6, 10)
    assert week_start(monday) == monday
    assert week_start(wednesday) == monday


def test_week_start_sunday_belongs_to_its_week():
    sunday = date(2026, 6, 14)
    assert week_start(sunday) == date(2026, 6, 8)


# --- Миссии -----------------------------------------------------------------


def test_weekly_missions_have_unique_codes():
    codes = [m.code for m in cfg.WEEKLY_MISSIONS]
    assert len(codes) == len(set(codes))


def test_weekly_mission_metrics_are_known():
    known = {
        cfg.MISSION_METRIC_FARM,
        cfg.MISSION_METRIC_CASES,
        cfg.MISSION_METRIC_DUEL_WIN,
        cfg.MISSION_METRIC_TREASURE,
        cfg.MISSION_METRIC_MESSAGES,
    }
    for m in cfg.WEEKLY_MISSIONS:
        assert m.metric in known
