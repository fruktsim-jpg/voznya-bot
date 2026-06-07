"""Слой доступа к данным кейсов.

Чтение определений кейсов, дроп-листов и истории открытий. Запись открытий —
в сервисе :mod:`app.features.cases.service` (единая атомарная точка); здесь
только чтения и вспомогательные выборки под блокировкой для самой транзакции
открытия.

Все функции принимают ``session: AsyncSession`` первым аргументом и не делают
commit (его выполняет middleware) — как в остальных репозиториях проекта.
Связи логические по ``code``/``id`` (без FK — конвенция проекта).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CaseDefinition,
    CaseOpening,
    CaseReward,
    GiftCatalog,
    InventoryItem,
)



async def get_active_cases(session: AsyncSession) -> list[CaseDefinition]:
    """Возвращает активные кейсы, доступные по расписанию (now в окне)."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(CaseDefinition)
        .where(CaseDefinition.is_active.is_(True))
        .where(
            (CaseDefinition.starts_at.is_(None))
            | (CaseDefinition.starts_at <= now)
        )
        .where(
            (CaseDefinition.ends_at.is_(None)) | (CaseDefinition.ends_at >= now)
        )
        .order_by(CaseDefinition.name)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_case_by_item_code(
    session: AsyncSession, item_code: str
) -> CaseDefinition | None:
    """Возвращает определение кейса по его item_code (или None)."""
    return await session.scalar(
        select(CaseDefinition).where(CaseDefinition.item_code == item_code)
    )


async def get_case_rewards(
    session: AsyncSession, case_item_code: str
) -> list[CaseReward]:
    """Возвращает дроп-лист кейса (все строки наград, в стабильном порядке)."""
    stmt = (
        select(CaseReward)
        .where(CaseReward.case_item_code == case_item_code)
        .order_by(CaseReward.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_item_meta_by_codes(
    session: AsyncSession, codes: list[str]
) -> dict[str, tuple[str, str]]:
    """Возвращает {code: (name, rarity)} для предметов каталога по их кодам.

    Нужно дроп-листу кейса, чтобы показывать НАЗВАНИЕ и РЕДКОСТЬ предмета вместо
    голого ``item_code``. Отсутствующие в каталоге коды просто не попадают в
    результат — вызывающая сторона делает фолбэк на сам код.
    """
    if not codes:
        return {}
    rows = await session.execute(
        select(InventoryItem.code, InventoryItem.name, InventoryItem.rarity).where(
            InventoryItem.code.in_(codes)
        )
    )
    return {row[0]: (row[1] or row[0], row[2] or "common") for row in rows}


async def get_gift_meta_by_codes(
    session: AsyncSession, codes: list[str]
) -> dict[str, str]:
    """Возвращает {code: name} для позиций ``gift_catalog`` по их кодам.

    Нужно дроп-листу/результату кейса, чтобы показывать НАЗВАНИЕ подарка
    (например «Бриллиант», «Telegram Premium 6 месяцев») вместо голого
    ``gift_diamond``. Отсутствующие коды не попадают в результат — вызывающая
    сторона делает фолбэк на сам код.
    """
    if not codes:
        return {}
    rows = await session.execute(
        select(GiftCatalog.code, GiftCatalog.name).where(
            GiftCatalog.code.in_(codes)
        )
    )
    return {row[0]: (row[1] or row[0]) for row in rows}


async def get_available_rewards_for_update(

    session: AsyncSession, case_item_code: str
) -> list[CaseReward]:
    """Возвращает доступные для выпадения награды ПОД БЛОКИРОВКОЙ строк.

    Используется внутри транзакции открытия: блокирует строки дроп-листа
    (``FOR UPDATE``), чтобы инкремент ``granted_count`` для лимиток был
    безопасен при гонках. Отсекает награды, чей лимит выпадений исчерпан.
    """
    stmt = (
        select(CaseReward)
        .where(CaseReward.case_item_code == case_item_code)
        .where(
            (CaseReward.max_global_supply.is_(None))
            | (CaseReward.granted_count < CaseReward.max_global_supply)
        )
        .order_by(CaseReward.id)
        .with_for_update()
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_recent_openings(
    session: AsyncSession, *, user_id: int | None = None, limit: int = 50
) -> list[CaseOpening]:
    """Возвращает последние открытия (опционально по игроку), новые сверху."""
    stmt = select(CaseOpening).order_by(CaseOpening.created_at.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(CaseOpening.user_id == user_id)
    return list((await session.execute(stmt)).scalars().all())


async def count_openings(session: AsyncSession, user_id: int) -> int:
    """Сколько кейсов открыл игрок (для профиля/достижений)."""
    total = await session.scalar(
        select(func.count())
        .select_from(CaseOpening)
        .where(CaseOpening.user_id == user_id)
    )
    return int(total or 0)
