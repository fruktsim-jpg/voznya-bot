"""Логика клада Возни.

Клад появляется случайно 2–4 раза в сутки (расписание планируется на день).
Первый, кто напишет ``/снять``, забирает награду. После этого клад
деактивируется до следующего появления.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.core.utils import get_tz, now_local, now_utc
from app.models import User
from app.models.treasure import TREASURE_ACTIVE, TREASURE_CLAIMED, TREASURE_EXPIRED, Treasure
from app.services.economy import change_balance
from app.settings import balance, texts

logger = get_logger(__name__)


@dataclass
class ClaimResult:
    """Результат попытки забрать клад."""

    status: str  # "none" / "claimed"
    reward: int = 0
    balance: int = 0
    fast: bool = False


async def claim_treasure(
    session: AsyncSession, user_id: int, chat_id: int
) -> ClaimResult:
    """Пытается забрать активный клад в чате.

    Использует блокировку строки, чтобы при одновременном ``/снять`` награду
    получил ровно один пользователь.
    """
    result = await session.execute(
        select(Treasure)
        .where(Treasure.chat_id == chat_id, Treasure.status == TREASURE_ACTIVE)
        .order_by(Treasure.spawned_at.desc())
        .limit(1)
        .with_for_update()
    )
    treasure = result.scalars().first()
    if treasure is None:
        return ClaimResult(status="none")

    claimed_at = now_utc()
    treasure.status = TREASURE_CLAIMED
    treasure.claimed_by = user_id
    treasure.claimed_at = claimed_at

    # Был ли клад забран почти мгновенно (для секретного достижения).
    # spawned_at хранится как timezone-aware (UTC), claimed_at — тоже.
    delta = (claimed_at - treasure.spawned_at).total_seconds()
    fast = 0 <= delta <= balance.TREASURE_FAST_SECONDS

    user = await change_balance(
        session, user_id, treasure.reward, "treasure", {"treasure_id": treasure.id}
    )
    user.treasures_found += 1

    return ClaimResult(
        status="claimed", reward=treasure.reward, balance=user.balance, fast=fast
    )


async def spawn_treasure(
    bot: Bot, sessionmaker: async_sessionmaker[AsyncSession], chat_id: int
) -> None:
    """Создаёт новый клад и публикует сообщение в чате.

    Если предыдущий клад так и не забрали, он помечается просроченным.
    """
    reward = random.randint(balance.TREASURE_REWARD_MIN, balance.TREASURE_REWARD_MAX)
    async with sessionmaker() as session:
        # Просрочиваем незабранные клады.
        old = await session.execute(
            select(Treasure).where(
                Treasure.chat_id == chat_id, Treasure.status == TREASURE_ACTIVE
            )
        )
        for prev in old.scalars().all():
            prev.status = TREASURE_EXPIRED

        treasure = Treasure(chat_id=chat_id, reward=reward, status=TREASURE_ACTIVE)
        session.add(treasure)
        await session.flush()

        sent = await bot.send_message(chat_id, random.choice(texts.TREASURE_SPAWN_VARIANTS))
        treasure.message_id = sent.message_id
        await session.commit()
    logger.info("Клад создан в чате %s (награда %s)", chat_id, reward)


def _random_times_today(count: int) -> list[datetime]:
    """Возвращает ``count`` случайных моментов сегодня (в будущем, локальное время)."""
    tz = get_tz()
    now = now_local()
    day_end = datetime.combine(now.date(), time(23, 59), tzinfo=tz)
    # Оставляем небольшой запас, чтобы клады не сыпались впритык к полуночи.
    earliest = now + timedelta(minutes=1)
    if earliest >= day_end:
        return []

    span = (day_end - earliest).total_seconds()
    moments = sorted(
        earliest + timedelta(seconds=random.uniform(0, span)) for _ in range(count)
    )
    return moments


def plan_daily_treasures(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    chat_id: int,
) -> None:
    """Планирует появления кладов на текущий день."""
    count = random.randint(
        balance.TREASURE_SPAWNS_PER_DAY_MIN, balance.TREASURE_SPAWNS_PER_DAY_MAX
    )
    moments = _random_times_today(count)
    for index, moment in enumerate(moments):
        scheduler.add_job(
            spawn_treasure,
            trigger="date",
            run_date=moment,
            args=[bot, sessionmaker, chat_id],
            id=f"treasure_{moment.date()}_{index}",
            replace_existing=True,
            misfire_grace_time=600,
        )
    logger.info("Запланировано кладов на сегодня: %s", len(moments))


def setup_treasure_scheduler(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    chat_id: int,
) -> None:
    """Регистрирует планирование кладов: на сегодня и ежедневно после полуночи."""
    plan_daily_treasures(scheduler, bot, sessionmaker, chat_id)
    scheduler.add_job(
        plan_daily_treasures,
        trigger="cron",
        hour=0,
        minute=5,
        args=[scheduler, bot, sessionmaker, chat_id],
        id="treasure_daily_planner",
        replace_existing=True,
    )
