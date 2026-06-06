"""Слой доступа к данным системы рейтинга MMR.

Аудит-журнал ``mmr_entries`` — источник правды (история всех изменений).
Текущее значение MMR денормализовано в ``users.mmr`` (проекция журнала): его
читают профиль, рейтинг, сайт и MMR-команды, чтобы не пересчитывать
``SUM(amount)`` при каждом запросе. Запись изменения (``add_entry``) обновляет
журнал и проекцию синхронно, в одной транзакции.

Все функции принимают ``session: AsyncSession`` первым аргументом и не делают
commit (его выполняет вызывающий код / middleware) — как в остальных
репозиториях проекта.

MMR изолирован: не трогает balance/transactions/репутацию/messages/
shop/inventory/gift/Combot. Из ``users`` пишется ТОЛЬКО поле ``mmr``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MmrEntry, User



@dataclass(frozen=True)
class MmrTopRow:
    """Строка топа рейтинга."""

    user_id: int
    first_name: str | None
    username: str | None
    mmr: int


async def get_mmr(session: AsyncSession, user_id: int) -> int:
    """Возвращает текущий рейтинг игрока из проекции ``users.mmr``.

    Дешёвое чтение одного поля вместо агрегата по журналу. Если строки игрока
    в ``users`` нет (теоретически возможно до первого начисления) — возвращает 0.
    """
    value = await session.scalar(
        select(User.mmr).where(User.user_id == user_id)
    )
    return int(value or 0)


async def recompute_mmr(session: AsyncSession, user_id: int) -> int:
    """Пересчитывает MMR из журнала (``SUM(amount)``) — медленный путь.

    Нужен для разовой инициализации проекции и для админ-сверки/починки, если
    проекция и журнал разошлись. В горячем пути НЕ используется.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(MmrEntry.amount), 0)).where(
            MmrEntry.player_id == user_id
        )
    )
    return int(total or 0)


async def add_entry(
    session: AsyncSession,
    *,
    player_id: int,
    amount: int,
    source: str,
    reason: str | None,
) -> None:
    """Добавляет одно изменение рейтинга в журнал И обновляет проекцию.

    Журнал ``mmr_entries`` — источник правды (аудит), ``users.mmr`` — текущее
    значение для быстрых чтений. Обе записи идут в одной транзакции (commit
    делает вызывающий код), поэтому они не могут разойтись.
    """
    session.add(
        MmrEntry(
            player_id=player_id,
            amount=amount,
            source=source,
            reason=reason,
        )
    )
    # Инкрементально двигаем проекцию. Атомарный UPDATE ... SET mmr = mmr + :d
    # на стороне БД (без read-modify-write в Python) — безопасно при гонках.
    await session.execute(
        update(User)
        .where(User.user_id == player_id)
        .values(mmr=User.mmr + amount)
    )


async def top_by_mmr(session: AsyncSession, limit: int) -> list[MmrTopRow]:
    """Возвращает топ игроков по рейтингу (по убыванию).

    Читает денормализованное ``users.mmr`` — без агрегата и JOIN по журналу.
    """
    stmt = (
        select(User.user_id, User.first_name, User.username, User.mmr)
        .where(User.mmr > 0)
        .order_by(User.mmr.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        MmrTopRow(
            user_id=row[0],
            first_name=row[1],
            username=row[2],
            mmr=int(row[3] or 0),
        )
        for row in rows
    ]



async def get_history(
    session: AsyncSession, user_id: int, limit: int = 20
) -> list[MmrEntry]:
    """Возвращает последние изменения рейтинга игрока (новые сверху).

    Полезно для отладки/админки и пересчёта; в командах V1 не используется.
    """
    stmt = (
        select(MmrEntry)
        .where(MmrEntry.player_id == user_id)
        .order_by(MmrEntry.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
