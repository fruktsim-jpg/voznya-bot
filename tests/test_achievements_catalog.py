"""Тесты целостности каталога достижений (без БД).

Проверяем, что новые категории Сезона 1 (сообщения, кейсы, ферма, подарки,
траты, коллекционер, сезон) корректно заведены, метрики совпадают с тем, что
умеет считать сборщик статистики, пороги отсортированы, коды уникальны, а
сезонные достижения исключены из требования «открыть всё».
"""

from __future__ import annotations

from app.features.achievements import service as ach_service
from app.settings.achievements import (
    ACHIEVEMENTS,
    ACHIEVEMENTS_BY_CODE,
    CORE_ACHIEVEMENT_CODES,
    CORE_EXCLUDED_CATEGORIES,
    METRIC_ALL,
    METRIC_EVENT,
)


# Метрики, которые реально считает _gather_stats (ключи возвращаемого словаря).
# Держим список явно — тест падёт, если достижение ссылается на метрику, для
# которой нет источника данных.
_SUPPORTED_METRICS = {
    "total_earned",
    "total_spent",
    "messages_count",
    "season_mmr",
    "farm_success_count",
    "casino_games_count",
    "duels_won",
    "treasures_found",
    "pidor_count",
    "max_farm_streak",
    "max_casino_loss",
    "casino_loss_streak",
    "duel_loss_streak",
    "marriages_count",
    "cases_opened",
    "gifts_received",
    "distinct_items",
    "login_streak",
}


def test_codes_are_unique():
    codes = [a.code for a in ACHIEVEMENTS]
    assert len(codes) == len(set(codes)), "Дублирующиеся коды достижений"


def test_every_metric_has_a_source():
    """Каждая метрическая ачивка ссылается на счётчик, который реально считается."""
    for a in ACHIEVEMENTS:
        if a.metric in (METRIC_ALL, METRIC_EVENT):
            continue
        assert a.metric in _SUPPORTED_METRICS, (
            f"{a.code}: метрика '{a.metric}' не считается в _gather_stats"
        )


def test_new_categories_present():
    cats = {a.category for a in ACHIEVEMENTS}
    for expected in (
        "messages",
        "cases",
        "farm",
        "gifts",
        "spending",
        "collection",
        "season",
    ):
        assert expected in cats, f"Нет категории {expected}"


def test_new_achievements_count_per_category():
    """Точное число новых достижений по ТЗ Сезона 1."""
    by_cat: dict[str, int] = {}
    for a in ACHIEVEMENTS:
        by_cat[a.category] = by_cat.get(a.category, 0) + 1
    assert by_cat["messages"] == 5
    assert by_cat["cases"] == 4
    assert by_cat["farm"] == 3
    assert by_cat["gifts"] == 3
    assert by_cat["spending"] == 3
    assert by_cat["collection"] == 3
    assert by_cat["season"] == 5


def test_thresholds_match_spec():
    assert [a.threshold for a in ACHIEVEMENTS if a.category == "messages"] == [
        100, 1000, 5000, 10000, 50000,
    ]
    assert [a.threshold for a in ACHIEVEMENTS if a.category == "cases"] == [
        10, 50, 100, 500,
    ]
    assert [a.threshold for a in ACHIEVEMENTS if a.category == "farm"] == [
        50, 250, 1000,
    ]
    assert [a.threshold for a in ACHIEVEMENTS if a.category == "gifts"] == [
        1, 10, 50,
    ]
    assert [a.threshold for a in ACHIEVEMENTS if a.category == "spending"] == [
        1000, 5000, 25000,
    ]
    assert [a.threshold for a in ACHIEVEMENTS if a.category == "collection"] == [
        5, 10, 25,
    ]


def test_season_thresholds_match_divisions():
    """Сезонные ачивки выровнены по порогам дивизионов (Silver..Master)."""
    from app.settings import season as season_cfg

    season_thresholds = [
        a.threshold for a in ACHIEVEMENTS if a.category == "season"
    ]
    # Silver(500), Gold(1500), Platinum(3500), Diamond(7000), Master(12000).
    division_mins = [d.min_mmr for d in season_cfg.DIVISIONS if d.min_mmr > 0]
    assert season_thresholds == division_mins


def test_season_excluded_from_core():
    """Сезонные достижения НЕ нужны для «Меллстрой Возни» (иначе недостижимо)."""
    assert "season" in CORE_EXCLUDED_CATEGORIES
    season_codes = {a.code for a in ACHIEVEMENTS if a.category == "season"}
    assert not (season_codes & CORE_ACHIEVEMENT_CODES)


def test_rewards_are_moderate():
    """Награды умеренные: ни одно новое достижение не платит больше 1000 ешек."""
    new_cats = {"messages", "cases", "farm", "gifts", "spending", "collection", "season"}
    for a in ACHIEVEMENTS:
        if a.category in new_cats:
            assert 0 < a.reward <= 1000, f"{a.code}: награда вне диапазона"


def test_gather_stats_keys_cover_supported_metrics():
    """Список поддерживаемых метрик в тесте совпадает с ключами _gather_stats.

    Защита от рассинхрона: если в сборщик добавят/уберут метрику, обновить и
    тест (он явно перечисляет источники данных).
    """
    import inspect

    src = inspect.getsource(ach_service._gather_stats)
    for metric in _SUPPORTED_METRICS:
        assert f'"{metric}"' in src, f"_gather_stats не возвращает {metric}"
