"""Экономика: единая точка изменения баланса ешек.

ВСЕ начисления и списания валюты обязаны проходить через
:func:`change_balance`. Это гарантирует:
* атомарность (блокировка строки пользователя через ``FOR UPDATE``);
* корректный учёт «всего заработано / всего потрачено»;
* запись каждой операции в журнал (леджер) транзакций.

Будущие механики (магазин, банк, лотереи) должны использовать этот сервис.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.models import Transaction, User

logger = get_logger(__name__)


class InsufficientFunds(Exception):
    """Бросается при попытке списать больше, чем есть на балансе."""

    def __init__(self, balance: int, requested: int) -> None:
        self.balance = balance
        self.requested = requested
        super().__init__(f"Недостаточно средств: есть {balance}, нужно {requested}")


async def change_balance(
    session: AsyncSession,
    user_id: int,
    amount: int,
    reason: str,
    meta: dict | None = None,
    allow_negative: bool = False,
) -> User:
    """Изменяет баланс пользователя на ``amount`` (+ начисление, − списание).

    Блокирует строку пользователя на время операции, чтобы исключить гонки
    (например, двойное начисление при быстром повторе команды).

    :param allow_negative: если False, баланс не может уйти в минус — будет
        брошено :class:`InsufficientFunds`.
    :raises InsufficientFunds: при недостатке средств и ``allow_negative=False``.
    """
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        # Пользователь должен существовать (создаётся в middleware),
        # но на всякий случай создаём запись, чтобы не потерять операцию.
        user = User(user_id=user_id, balance=0)
        session.add(user)
        await session.flush()
        user = await session.get(User, user_id, with_for_update=True)
        assert user is not None

    new_balance = user.balance + amount
    if new_balance < 0 and not allow_negative:
        raise InsufficientFunds(user.balance, -amount)

    user.balance = new_balance
    if amount > 0:
        user.total_earned += amount
    elif amount < 0:
        user.total_spent += -amount

    session.add(
        Transaction(user_id=user_id, amount=amount, reason=reason, meta=meta)
    )
    logger.debug(
        "balance change: user=%s amount=%s reason=%s -> balance=%s",
        user_id, amount, reason, user.balance,
    )
    return user


async def get_balance(session: AsyncSession, user_id: int) -> int:
    """Возвращает текущий баланс пользователя (0, если пользователя нет)."""
    user = await session.get(User, user_id)
    return user.balance if user else 0
