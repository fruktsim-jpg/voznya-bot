"""Форматирование внутренней валюты «ешки» с правильным склонением.

Единая точка для отображения сумм во всех механиках проекта.

Правила русского склонения:
* 1, 21, 101  → «ешка»  (но не 11)
* 2–4, 22–24  → «ешки»  (но не 12–14)
* 0, 5–20, 11–14, 25–30 → «ешек»
"""

from __future__ import annotations

# Три формы слова: для 1, для 2–4, для 5+ (и для 11–14).
_FORMS = ("ешка", "ешки", "ешек")


def plural_ezhki(amount: int) -> str:
    """Возвращает правильную форму слова «ешка» для числа."""
    n = abs(int(amount))
    if n % 100 in (11, 12, 13, 14):
        return _FORMS[2]
    last = n % 10
    if last == 1:
        return _FORMS[0]
    if last in (2, 3, 4):
        return _FORMS[1]
    return _FORMS[2]


def format_number(amount: int) -> str:
    """Форматирует число с разделением тысяч неразрывным пробелом."""
    return f"{int(amount):,}".replace(",", "\u202f")


def money(amount: int) -> str:
    """Возвращает сумму с числом и правильным склонением, например «1 234 ешки»."""
    return f"{format_number(amount)} {plural_ezhki(amount)}"
