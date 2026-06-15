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
from app.settings import dynamic

logger = get_logger(__name__)

# «Продуктивные» источники дохода формируют прогрессию (total_earned/spent
# и, как следствие, титулы). Азартные игры (казино, дуэли) перераспределяют
# валюту и НЕ должны раздувать прогрессию — они влияют только на баланс
# и недельный рейтинг. Это сознательное экономическое решение (см. ECONOMY.md).
PRODUCTIVE_REASONS = {
    "farm",
    "treasure",
    "achievement",
    "nomination",
    "admin",
    # Сезонные продуктивные источники (Сезон 1): ежедневная награда, недельные
    # задания, сезонные награды в финале. Идут в прогрессию, но закапаны
    # дизайном (см. app/settings/season.py) — не печатают бесконечно.
    "daily",
    "mission",
    "season_reward",
}

# Источники, к которым применяется глобальный множитель ешек (app_settings:
# modifier.eshki). Это «зарабатываемые» награды — ферма, клад, ачивки, дейлик,
# миссии, сезонные награды, награды за событие/исход дуэли. СОЗНАТЕЛЬНО НЕ
# масштабируются: казино (перераспределение), покупки, передачи ешек между
# игроками и ПРОДАЖА предметов (reason="reward"/"item_sell") — иначе множитель
# ломал бы экономику и открывал дюп (продать вдвое дороже во время ивента).
MULTIPLIED_REASONS = {
    "farm",
    "treasure",
    "achievement",
    "nomination",
    "daily",
    "mission",
    "season_reward",
    "event_reward",
    "duel_reward",
    "family_reward",
}



class InsufficientFunds(Exception):
    """Бросается при попытке списать больше, чем есть на балансе."""

    def __init__(self, balance: int, requested: int) -> None:
        self.balance = balance
        self.requested = requested
        super().__init__(f"Недостаточно средств: есть {balance}, нужно {requested}")


async def _apply_balance_change(
    session: AsyncSession,
    user_id: int,
    amount: int,
    reason: str,
    meta: dict | None,
    allow_negative: bool,
) -> tuple[User, Transaction]:
    """Ядро изменения баланса: блокировка, проверка, проводка в леджер.

    Возвращает пользователя и созданную (но ещё не сброшенную) транзакцию.
    Используется обёртками :func:`change_balance` и :func:`change_balance_tx`.
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

    # Глобальный множитель ешек из админки (app_settings: modifier.eshki).
    # Применяется ТОЛЬКО к положительным начислениям «зарабатываемых» наград
    # (MULTIPLIED_REASONS). Дефолт 1.0 ничего не меняет. Прибавка (×множитель −
    # базовая сумма) логируется в meta для прозрачности леджера.
    if amount > 0 and reason in MULTIPLIED_REASONS:
        multiplier = await dynamic.get_float(session, "modifier.eshki", 1.0)
        if multiplier > 0 and multiplier != 1.0:
            boosted = int(round(amount * multiplier))
            if boosted != amount:
                meta = {**(meta or {}), "base_amount": amount, "eshki_multiplier": multiplier}
                amount = boosted

    new_balance = user.balance + amount
    if new_balance < 0 and not allow_negative:
        raise InsufficientFunds(user.balance, -amount)

    user.balance = new_balance
    # В прогрессию (total_earned/total_spent) попадают только продуктивные
    # источники; азартные игры меняют лишь баланс.
    if reason in PRODUCTIVE_REASONS:
        if amount > 0:
            user.total_earned += amount
        elif amount < 0:
            user.total_spent += -amount

    tx = Transaction(user_id=user_id, amount=amount, reason=reason, meta=meta)
    session.add(tx)
    logger.debug(
        "balance change: user=%s amount=%s reason=%s -> balance=%s",
        user_id, amount, reason, user.balance,
    )
    return user, tx


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
    user, _ = await _apply_balance_change(
        session, user_id, amount, reason, meta, allow_negative
    )
    return user


async def change_balance_tx(
    session: AsyncSession,
    user_id: int,
    amount: int,
    reason: str,
    meta: dict | None = None,
    allow_negative: bool = False,
) -> Transaction:
    """Как :func:`change_balance`, но возвращает саму проводку.

    Делает ``session.flush()``, чтобы у транзакции был проставлен ``id`` — его
    можно связать с другими леджерами (например, ``case_openings.transaction_id``
    или ``purchase_history.transaction_id``). Семантика и блокировки идентичны
    ``change_balance``; существующие вызовы не затрагиваются.
    """
    _, tx = await _apply_balance_change(
        session, user_id, amount, reason, meta, allow_negative
    )
    await session.flush()
    return tx



async def get_balance(session: AsyncSession, user_id: int) -> int:
    """Возвращает текущий баланс пользователя (0, если пользователя нет)."""
    user = await session.get(User, user_id)
    return user.balance if user else 0
