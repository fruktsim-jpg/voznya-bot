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


# 8 канонических кодов лимиток (миграции 0029/0030). Дублируется здесь, чтобы
# отличать лимитку от обычного подарка в агрегатах статистики без join.
_LIMITED_CODES = {
    "gift_xmas_bear",
    "gift_xmas_tree",
    "gift_valentine_bear",
    "gift_valentine_heart",
    "gift_spring_bear",
    "gift_lucky_bear",
    "gift_clown_bear",
    "gift_easter_bear",
}
_PREMIUM_CODES = {"gift_premium_3m", "gift_premium_6m"}


async def get_case_stats(session: AsyncSession, case_item_code: str) -> dict:
    """Сводная статистика по кейсу из ``case_openings`` (Release 2.2 P-статистика).

    Возвращает: число открытий, потрачено ешек (цена × открытия), сколько
    Premium / лимиток / джекпотов выпало. Всё восстановимо из леджера открытий,
    отдельной таблицы статистики не вводим.
    """
    case = await get_case_by_item_code(session, case_item_code)
    price = int(case.open_cost_amount) if case else 0

    rows = await session.execute(
        select(
            CaseOpening.reward_kind,
            CaseOpening.reward_item_code,
            CaseOpening.qty,
            func.count().label("cnt"),
        )
        .where(CaseOpening.case_item_code == case_item_code)
        .group_by(CaseOpening.reward_kind, CaseOpening.reward_item_code, CaseOpening.qty)
    )

    openings = 0
    premium = 0
    limited = 0
    jackpot = 0
    for kind, code, qty, cnt in rows:
        cnt = int(cnt or 0)
        openings += cnt
        if kind == "tg_gift" and code in _PREMIUM_CODES:
            premium += cnt
        elif kind == "tg_gift" and code in _LIMITED_CODES:
            limited += cnt * int(qty or 1)
        # Денежный джекпот фиксируем по крупной сумме (мега-приз).
    # Денежные джекпоты — по флагу is_jackpot выпавшей строки недоступны в
    # CaseOpening, поэтому считаем по reward_id-наградам с is_jackpot отдельно.
    jackpot = await session.scalar(
        select(func.count())
        .select_from(CaseOpening)
        .join(CaseReward, CaseReward.id == CaseOpening.reward_id)
        .where(CaseOpening.case_item_code == case_item_code)
        .where(CaseReward.is_jackpot.is_(True))
    )

    return {
        "case_item_code": case_item_code,
        "openings": openings,
        "eshki_spent": openings * price,
        "premium": premium,
        "limited": limited,
        "jackpot": int(jackpot or 0),
    }


async def get_top_openings(
    session: AsyncSession, *, case_item_code: str | None = None, limit: int = 10
) -> list[CaseOpening]:
    """Самые «крупные»/последние открытия для витрины статистики.

    Сортировка по дате (новые сверху) — «последние крупные выпадения». Фильтр по
    кейсу опционален. Отбор «крупности» (gift/premium/джекпот) — на стороне
    вызывающего кода/сайта по reward_kind/reward_item_code.
    """
    stmt = select(CaseOpening).order_by(CaseOpening.created_at.desc()).limit(limit)
    if case_item_code is not None:
        stmt = stmt.where(CaseOpening.case_item_code == case_item_code)
    return list((await session.execute(stmt)).scalars().all())


