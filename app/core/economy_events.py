"""Система событий экономики — единые типы начислений/списаний ешек.

Все движения валюты проходят через существующий леджер ``transactions``
(``app/models/transaction.py``): одно событие = одна строка с ``reason`` из этого
модуля, знаковым ``amount`` (+начисление / −списание) и деталями в ``meta``.
Баланс не дублируется — он живёт в ``users`` и меняется через ``change_balance``.

Это НЕ новая таблица: модуль фиксирует канонический набор причин верхнего уровня
и хелперы, чтобы бот, магазин, подарки и админка писали в леджер единообразно.
Совместимо с типами, зафиксированными в ``ADMIN_PLATFORM.md`` §5.
"""

from __future__ import annotations

# Типы экономических событий → пишутся в transactions.reason.
EVENT_REWARD = "reward"                 # базовая награда (ферма, клад)
EVENT_EVENT_REWARD = "event_reward"     # награда за игровое событие
EVENT_REFERRAL_REWARD = "referral_reward"  # реферальная награда
EVENT_ADMIN_REWARD = "admin_reward"     # ручное начисление администратором
EVENT_DUEL_REWARD = "duel_reward"       # исход дуэли
EVENT_FAMILY_REWARD = "family_reward"   # семейные начисления
EVENT_PURCHASE = "purchase"             # списание за покупку в магазине
EVENT_GIFT = "gift"                     # передача ешек между игроками

# Полный набор для валидации.
ECONOMY_EVENTS = (
    EVENT_REWARD,
    EVENT_EVENT_REWARD,
    EVENT_REFERRAL_REWARD,
    EVENT_ADMIN_REWARD,
    EVENT_DUEL_REWARD,
    EVENT_FAMILY_REWARD,
    EVENT_PURCHASE,
    EVENT_GIFT,
)

# События, требующие записи в audit_log (инициированы администрацией).
ADMIN_EVENTS = (EVENT_ADMIN_REWARD,)


def is_economy_event(reason: str) -> bool:
    """Проверяет, что причина транзакции — известное событие экономики."""
    return reason in ECONOMY_EVENTS


def requires_audit(reason: str) -> bool:
    """Нужно ли логировать событие в audit_log (админ-инициированное)."""
    return reason in ADMIN_EVENTS
