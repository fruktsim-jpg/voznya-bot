"""Система титулов Возни — ранги на основе баланса ешек.

==============================================================================
ЭТУ ТАБЛИЦУ МОЖНО РЕДАКТИРОВАТЬ, не зная программирования.
Каждая строка — это (минимальный баланс, эмодзи, название титула).
Чтобы добавить новый ранг, просто добавьте строку и сохраните порядок
по возрастанию баланса. После изменения перезапустите бота.
==============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Title:
    """Один титул (ранг)."""

    min_balance: int
    emoji: str
    name: str

    @property
    def label(self) -> str:
        """Эмодзи + название, например «💊 Аптекарь»."""
        return f"{self.emoji} {self.name}"


# Список титулов по возрастанию порога баланса.
TITLES: list[Title] = [
    Title(0, "🌱", "Щавель"),
    Title(100, "🍑", "Персик"),
    Title(250, "🐀", "Гой"),
    Title(500, "🍺", "Бурмалда"),
    Title(1000, "💊", "Аптекарь"),
    Title(2500, "🎰", "Лудоман"),
    Title(5000, "⚔️", "Возняк"),
    Title(10000, "🏆", "Авторитет Возни"),
    Title(25000, "👑", "Король Возни"),
    Title(50000, "☢️", "Легенда Возни"),
]


def get_title(balance: int) -> Title:
    """Возвращает текущий титул по балансу."""
    current = TITLES[0]
    for title in TITLES:
        if balance >= title.min_balance:
            current = title
        else:
            break
    return current


def get_next_title(balance: int) -> Title | None:
    """Возвращает следующий титул (или None, если достигнут максимум)."""
    for title in TITLES:
        if title.min_balance > balance:
            return title
    return None
