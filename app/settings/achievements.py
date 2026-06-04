"""Каталог достижений Возни.

==============================================================================
ЭТОТ КАТАЛОГ МОЖНО РАСШИРЯТЬ.
Каждое достижение задаётся: код, эмодзи, название, описание, метрика,
порог и награда (в ешках, 0 — без награды).

Доступные метрики (по чему считается достижение):
  total_earned        — всего заработано ешек
  farm_success_count  — успешных ферм
  casino_games_count  — сыграно игр в казино
  duels_won           — побед в дуэлях
  treasures_found     — найдено кладов
  marriages_count     — заключено браков
  all                 — открыты все остальные достижения (особая метрика)
==============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass

METRIC_ALL = "all"


@dataclass(frozen=True)
class Achievement:
    """Одно достижение."""

    code: str
    emoji: str
    name: str
    description: str
    metric: str
    threshold: int
    reward: int = 0

    @property
    def label(self) -> str:
        """Эмодзи + название."""
        return f"{self.emoji} {self.name}"


# Порядок важен: «Легенда Возни» (метрика all) должна идти последней.
ACHIEVEMENTS: list[Achievement] = [
    Achievement("first_ezhka", "🌱", "Первая ешка", "Заработать первую ешку",
                "total_earned", 1, reward=10),
    Achievement("farmer", "💊", "Фермер", "10 успешных ферм",
                "farm_success_count", 10, reward=50),
    Achievement("baron", "💊", "Барон", "100 успешных ферм",
                "farm_success_count", 100, reward=200),
    Achievement("ludoman", "🎰", "Лудоман", "10 игр в казино",
                "casino_games_count", 10, reward=100),
    Achievement("casino_grandpa", "🎰", "Казиношный дед", "100 игр в казино",
                "casino_games_count", 100, reward=300),
    Achievement("duelist", "⚔️", "Дуэлянт", "1 победа в дуэли",
                "duels_won", 1, reward=100),
    Achievement("gladiator", "⚔️", "Гладиатор", "25 побед в дуэлях",
                "duels_won", 25, reward=500),
    Achievement("thousandaire", "💰", "Тысячник", "Заработать 1000 ешек",
                "total_earned", 1000, reward=250),
    Achievement("magnate", "💰", "Магнат", "Заработать 10000 ешек",
                "total_earned", 10000, reward=1000),
    Achievement("treasure_hunter", "📦", "Кладоискатель", "Найти 1 клад",
                "treasures_found", 1, reward=100),
    Achievement("treasure_master", "📦", "Охотник за кладом", "Найти 10 кладов",
                "treasures_found", 10, reward=400),
    Achievement("true_love", "💍", "Любовь существует", "Заключить первый брак",
                "marriages_count", 1, reward=50),
    Achievement("legend", "🏆", "Легенда Возни", "Получить все достижения",
                METRIC_ALL, 0, reward=1000),
]

# Быстрый доступ по коду.
ACHIEVEMENTS_BY_CODE: dict[str, Achievement] = {a.code: a for a in ACHIEVEMENTS}
