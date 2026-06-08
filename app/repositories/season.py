"""Слой доступа к данным сезонной системы.

Зеркалит подход ``repositories.mmr``: журнал ``season_mmr_entries`` — источник
правды сезонного MMR, ``users.season_mmr`` — денормализованная проекция для
быстрых чтений (карточка сезона, дивизион, рейтинг). Сезон активен ровно один.

Все функции принимают ``session: AsyncSession`` и НЕ делают commit (его делает
вызывающий код / middleware) — конвенция проекта.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select, update

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DailyClaim,
    LoginStreak,
    MmrEntry,
    Season,
    SeasonMmrEntry,
    SeasonTitleAward,
    User,
    WeeklyMissionProgress,
)



@dataclass(frozen=True)
class SeasonTopRow:
    """Строка сезонного рейтинга по season MMR."""

    user_id: int
    first_name: str | None
    username: str | None
    season_mmr: int


# --- Сезон ------------------------------------------------------------------


async def get_active_season(session: AsyncSession) -> Season | None:
    """Возвращает активный сезон (или None, если сезон не запущен)."""
    return await session.scalar(
        select(Season).where(Season.is_active.is_(True)).limit(1)
    )


async def start_season(
    session: AsyncSession, *, name: str, ends_at: datetime
) -> Season:
    """Стартует новый сезон, деактивируя все прочие. НЕ делает commit."""
    await session.execute(update(Season).values(is_active=False))
    season = Season(name=name, ends_at=ends_at, is_active=True)
    session.add(season)
    await session.flush()
    return season


async def finalize_season(session: AsyncSession, season_id: int) -> None:
    """Помечает сезон финализированным (is_active=False, finalized_at=now)."""
    await session.execute(
        update(Season)
        .where(Season.id == season_id)
        .values(is_active=False, finalized_at=datetime.now(timezone.utc))
    )


# --- Сезонный MMR -----------------------------------------------------------


async def get_season_mmr(session: AsyncSession, user_id: int) -> int:
    """Текущий сезонный MMR игрока из проекции ``users.season_mmr``."""
    value = await session.scalar(
        select(User.season_mmr).where(User.user_id == user_id)
    )
    return int(value or 0)


async def add_season_mmr(
    session: AsyncSession,
    *,
    season_id: int,
    player_id: int,
    amount: int,
    source: str,
    reason: str | None = None,
) -> None:
    """Пишет изменение сезонного MMR в журнал И двигает проекцию атомарно."""
    session.add(
        SeasonMmrEntry(
            season_id=season_id,
            player_id=player_id,
            amount=amount,
            source=source,
            reason=reason,
        )
    )
    await session.execute(
        update(User)
        .where(User.user_id == player_id)
        .values(season_mmr=User.season_mmr + amount)
    )


async def top_by_season_mmr(
    session: AsyncSession, limit: int
) -> list[SeasonTopRow]:
    """Топ игроков по сезонному MMR (по убыванию)."""
    stmt = (
        select(User.user_id, User.first_name, User.username, User.season_mmr)
        .where(User.season_mmr > 0)
        .order_by(User.season_mmr.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        SeasonTopRow(
            user_id=row[0],
            first_name=row[1],
            username=row[2],
            season_mmr=int(row[3] or 0),
        )
        for row in rows
    ]


# --- Антиабуз: дуэль-фарм MMR -----------------------------------------------


async def duel_mmr_grants_today(
    session: AsyncSession, *, player_id: int, since: datetime
) -> tuple[int, set[int]]:
    """Сколько раз игроку начисляли MMR за дуэли с момента ``since`` и с кем.

    Источник — журнал ``mmr_entries`` (source='duel'). В ``reason`` дуэль пишет
    ``"win:<opp>"`` / ``"participation:<opp>"`` — оппонент закодирован, чтобы
    проверять «разные оппоненты». Возвращает ``(count, opponent_ids)``.
    """
    rows = (
        await session.execute(
            select(MmrEntry.reason).where(
                MmrEntry.player_id == player_id,
                MmrEntry.source == "duel",
                MmrEntry.created_at >= since,
            )
        )
    ).all()
    opponents: set[int] = set()
    for (reason,) in rows:
        if not reason:
            continue
        _, _, opp = reason.partition(":")
        if opp.isdigit():
            opponents.add(int(opp))
    return len(rows), opponents


# --- Сезонные титулы --------------------------------------------------------



async def award_season_title(
    session: AsyncSession, *, season_id: int, player_id: int, code: str
) -> None:
    """Выдаёт сезонный титул (идемпотентно — uniq по season/player/code)."""
    session.add(
        SeasonTitleAward(season_id=season_id, player_id=player_id, code=code)
    )


# --- Login streak + daily ---------------------------------------------------


async def get_streak(session: AsyncSession, user_id: int) -> LoginStreak | None:
    """Возвращает запись серии заходов игрока (или None)."""
    return await session.scalar(
        select(LoginStreak).where(LoginStreak.player_id == user_id)
    )


async def touch_streak(
    session: AsyncSession, *, user_id: int, today: date
) -> tuple[int, bool]:
    """Обновляет серию заходов на сегодня.

    Возвращает ``(current_streak, is_new_day)``. ``is_new_day`` = True, если это
    первый заход в сегодняшний календарный день (тогда серия выросла или
    сбросилась). Если уже заходил сегодня — серия не меняется, возвращается
    текущая и ``False``.
    """
    streak = await get_streak(session, user_id)
    if streak is None:
        session.add(
            LoginStreak(
                player_id=user_id,
                last_login_date=today,
                current_streak=1,
                best_streak=1,
            )
        )
        return 1, True

    if streak.last_login_date == today:
        return streak.current_streak, False

    # Новый день: серия растёт при заходе «вчера», иначе сбрасывается на 1.
    if streak.last_login_date is not None and (
        today - streak.last_login_date
    ).days == 1:
        new_streak = streak.current_streak + 1
    else:
        new_streak = 1

    streak.current_streak = new_streak
    streak.last_login_date = today
    if new_streak > streak.best_streak:
        streak.best_streak = new_streak
    return new_streak, True


async def has_claimed_today(
    session: AsyncSession, *, user_id: int, today: date
) -> bool:
    """Забирал ли игрок ежедневную награду сегодня."""
    found = await session.scalar(
        select(DailyClaim.id).where(
            DailyClaim.player_id == user_id, DailyClaim.claim_date == today
        )
    )
    return found is not None


async def record_daily_claim(
    session: AsyncSession,
    *,
    user_id: int,
    today: date,
    amount: int,
    streak_day: int,
) -> None:
    """Фиксирует получение ежедневной награды (uniq player+date)."""
    session.add(
        DailyClaim(
            player_id=user_id,
            claim_date=today,
            amount=amount,
            streak_day=streak_day,
        )
    )


# --- Weekly missions --------------------------------------------------------


async def bump_mission(
    session: AsyncSession,
    *,
    user_id: int,
    week_start: date,
    mission_code: str,
    delta: int = 1,
) -> WeeklyMissionProgress:
    """Инкрементит прогресс задания, создавая строку при первом обращении."""
    row = await session.scalar(
        select(WeeklyMissionProgress).where(
            WeeklyMissionProgress.player_id == user_id,
            WeeklyMissionProgress.week_start == week_start,
            WeeklyMissionProgress.mission_code == mission_code,
        )
    )
    if row is None:
        row = WeeklyMissionProgress(
            player_id=user_id,
            week_start=week_start,
            mission_code=mission_code,
            progress=delta,
        )
        session.add(row)
        await session.flush()
        return row
    row.progress += delta
    return row


async def get_week_missions(
    session: AsyncSession, *, user_id: int, week_start: date
) -> list[WeeklyMissionProgress]:
    """Все строки прогресса заданий игрока за неделю."""
    stmt = select(WeeklyMissionProgress).where(
        WeeklyMissionProgress.player_id == user_id,
        WeeklyMissionProgress.week_start == week_start,
    )
    return list((await session.execute(stmt)).scalars().all())


async def mark_mission_claimed(
    session: AsyncSession, *, progress_id: int
) -> None:
    """Помечает задание как выполненное и награждённое (claimed_at=now)."""
    await session.execute(
        update(WeeklyMissionProgress)
        .where(WeeklyMissionProgress.id == progress_id)
        .values(claimed_at=datetime.now(timezone.utc))
    )
