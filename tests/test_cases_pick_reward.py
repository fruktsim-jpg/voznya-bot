"""Юнит-тесты взвешенного выбора награды кейса (``_pick_reward``).

Чистая функция (без БД): проверяем корректность диапазона ``roll``, выбор по
накопительной сумме весов и грубое соответствие эмпирического распределения
заданным весам. Запуск (в Docker, где есть Python и зависимости):

    docker compose exec bot pytest tests/test_cases_pick_reward.py -q

pytest — стандартный фреймворк проекта (см. AGENTS.md). Тест не требует БД и
aiogram: он импортирует только сервис кейсов.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.features.cases.service import _pick_reward


@dataclass
class FakeReward:
    """Минимальный дубль CaseReward для теста (нужны только id и weight)."""

    id: int
    weight: int


def test_pick_reward_roll_in_range() -> None:
    rewards = [FakeReward(1, 10), FakeReward(2, 30), FakeReward(3, 60)]
    for _ in range(1000):
        reward, roll, total = _pick_reward(rewards)  # type: ignore[arg-type]
        assert total == 100
        assert 0 <= roll < total
        assert reward in rewards


def test_pick_reward_single_row_always_selected() -> None:
    only = [FakeReward(7, 5)]
    for _ in range(100):
        reward, roll, total = _pick_reward(only)  # type: ignore[arg-type]
        assert reward.id == 7
        assert total == 5
        assert 0 <= roll < 5


def test_pick_reward_distribution_matches_weights() -> None:
    # Веса 10/30/60 → ожидаемые доли 0.1/0.3/0.6. Допуск широкий: проверяем
    # отсутствие грубых перекосов, а не точную статистику.
    rewards = [FakeReward(1, 10), FakeReward(2, 30), FakeReward(3, 60)]
    n = 20000
    counts: Counter[int] = Counter()
    for _ in range(n):
        reward, _, _ = _pick_reward(rewards)  # type: ignore[arg-type]
        counts[reward.id] += 1

    assert abs(counts[1] / n - 0.10) < 0.03
    assert abs(counts[2] / n - 0.30) < 0.04
    assert abs(counts[3] / n - 0.60) < 0.04


def test_pick_reward_cumulative_boundaries() -> None:
    # Каждый roll попадает ровно в один интервал накопительной суммы.
    rewards = [FakeReward(1, 1), FakeReward(2, 1), FakeReward(3, 1)]
    # Прогон даёт все три исхода при достаточном числе попыток.
    seen = {_pick_reward(rewards)[0].id for _ in range(200)}  # type: ignore[arg-type]
    assert seen == {1, 2, 3}
