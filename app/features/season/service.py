"""Бизнес-логика сезона: старт/финал, daily reward, weekly-миссии, дивизионы.

Начисление сезонного MMR происходит в ``mmr.service.award_mmr`` (зеркалит
lifetime). Здесь — то, что специфично для сезона: ежедневная награда со стриком,
прогресс/выдача недельных заданий, определение победителей и выдача наград в
финале. Все функции НЕ делают commit — его делает вызывающий код/middleware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import season as season_repo
from app.services.economy import change_balance
from app.settings import season as cfg


# --- Вспомогательные даты ---------------------------------------------------


def today_utc() -> date:
    """Текущая календарная дата в UTC (единый ключ для daily/streak)."""
    return datetime.now(timezone.utc).date()


def week_start(d: date) -> date:
    """Понедельник недели, которой принадлежит дата (ключ weekly-миссий)."""
    return d - timedelta(days=d.weekday())


# --- Старт / финал сезона ---------------------------------------------------


async def start_new_season(session: AsyncSession, *, name: str) -> int:
    """Стартует новый сезон длиной SEASON_LENGTH_DAYS. Возвращает его id."""
    ends_at = datetime.now(timezone.utc) + timedelta(
        days=cfg.SEASON_LENGTH_DAYS
    )
    season = await season_repo.start_season(session, name=name, ends_at=ends_at)
    return season.id


@dataclass(frozen=True)
class SeasonWinner:
    """Победитель/призёр сезона с присвоенными титулами."""

    user_id: int
    rank: int
    season_mmr: int
    division: str
    titles: list[str]


async def finalize_active_season(
    session: AsyncSession, *, top_n: int = 50
) -> list[SeasonWinner]:
    """Закрывает активный сезон: определяет победителей и выдаёт награды.

    Выдаёт: ешки по итоговому дивизиону (``Division.reward_eshki``) и сезонные
    титулы по условиям ``SEASON_TITLES`` (rank:N / division:Name). Возвращает
    список призёров для объявления. Если активного сезона нет — пустой список.
    """
    season = await season_repo.get_active_season(session)
    if season is None:
        return []

    top = await season_repo.top_by_season_mmr(session, top_n)
    winners: list[SeasonWinner] = []

    for idx, row in enumerate(top):
        rank = idx + 1
        division = cfg.get_division(row.season_mmr)

        # 1. Награда ешками по дивизиону (productive — идёт в total_earned).
        if division.reward_eshki > 0:
            await change_balance(
                session,
                row.user_id,
                division.reward_eshki,
                reason="season_reward",
                meta={
                    "season_id": season.id,
                    "division": division.name,
                    "rank": rank,
                },
            )

        # 2. Сезонные титулы по условиям.
        titles: list[str] = []
        for title in cfg.SEASON_TITLES:
            if _title_matches(title.condition, rank=rank, division=division.name):
                await season_repo.award_season_title(
                    session,
                    season_id=season.id,
                    player_id=row.user_id,
                    code=title.code,
                )
                titles.append(title.code)

        if division.reward_eshki > 0 or titles:
            winners.append(
                SeasonWinner(
                    user_id=row.user_id,
                    rank=rank,
                    season_mmr=row.season_mmr,
                    division=division.name,
                    titles=titles,
                )
            )

    await season_repo.finalize_season(session, season.id)

    # Событие мира: сезон завершён (легендарное, без конкретного actor).
    from app.services import world_events

    champion = winners[0] if winners else None
    await world_events.emit_safe(
        session,
        type=world_events.EVENT_SEASON_ENDED,
        actor_id=champion.user_id if champion else None,
        ref_table="seasons",
        ref_id=season.id,
        meta={
            "season_id": season.id,
            "season_name": getattr(season, "name", None),
            "winners": len(winners),
            "champion_division": champion.division if champion else None,
        },
    )
    return winners


def _title_matches(condition: str, *, rank: int, division: str) -> bool:
    """Проверяет условие сезонного титула (``rank:N`` / ``division:Name``)."""
    kind, _, value = condition.partition(":")
    if kind == "rank":
        try:
            return rank <= int(value)
        except ValueError:
            return False
    if kind == "division":
        return division == value
    return False


# --- Daily reward + streak --------------------------------------------------


@dataclass(frozen=True)
class DailyResult:
    """Итог попытки забрать ежедневную награду."""

    claimed: bool          # выдана ли награда сейчас
    already: bool          # уже забирал сегодня
    amount: int            # сколько начислено (0, если already)
    streak: int            # текущая серия заходов


async def claim_daily(session: AsyncSession, user_id: int) -> DailyResult:
    """Выдаёт ежедневную награду (раз в календарный день), двигая стрик.

    Защита от повторного получения — уникальный ключ (player_id, claim_date) в
    ``daily_claims`` + проверка ``has_claimed_today``. Размер награды зависит от
    дня серии (``daily_reward_for_streak``).
    """
    today = today_utc()

    # Сначала двигаем стрик (фиксирует факт захода в этот день).
    streak, _is_new_day = await season_repo.touch_streak(
        session, user_id=user_id, today=today
    )

    if await season_repo.has_claimed_today(session, user_id=user_id, today=today):
        return DailyResult(claimed=False, already=True, amount=0, streak=streak)

    # Размер награды: по умолчанию зависит от дня серии (daily_reward_for_streak).
    # Админка может задать ПЛОСКИЙ размер через app_settings: daily.reward —
    # тогда он переопределяет стрик-таблицу. Сентинел -1 = ключ не задан.
    from app.settings import dynamic

    override = await dynamic.get_int(session, "daily.reward", -1)
    if override >= 0:
        amount = override
    else:
        amount = cfg.daily_reward_for_streak(streak)
    await change_balance(
        session,
        user_id,
        amount,
        reason="daily",
        meta={"streak": streak},
    )
    await season_repo.record_daily_claim(
        session, user_id=user_id, today=today, amount=amount, streak_day=streak
    )
    return DailyResult(claimed=True, already=False, amount=amount, streak=streak)


# --- Weekly missions --------------------------------------------------------


async def progress_mission(
    session: AsyncSession, *, user_id: int, metric: str, delta: int = 1
) -> list[str]:
    """Двигает прогресс всех заданий недели по метрике ``metric``.

    Если задание достигло цели и ещё не было награждено — выдаёт награду
    (ешки + сезонный MMR) и помечает claimed. Возвращает коды только что
    ВЫПОЛНЕННЫХ заданий (для уведомления). Идемпотентно по ``claimed_at``.
    """
    week = week_start(today_utc())
    completed: list[str] = []

    for mission in cfg.WEEKLY_MISSIONS:
        if mission.metric != metric:
            continue
        row = await season_repo.bump_mission(
            session,
            user_id=user_id,
            week_start=week,
            mission_code=mission.code,
            delta=delta,
        )
        if row.claimed_at is None and row.progress >= mission.target:
            await _grant_mission_reward(session, user_id, mission)
            await season_repo.mark_mission_claimed(session, progress_id=row.id)
            completed.append(mission.code)

    return completed


async def _grant_mission_reward(
    session: AsyncSession, user_id: int, mission: cfg.Mission
) -> None:
    """Выдаёт награду за выполненное недельное задание (ешки + season MMR)."""
    if mission.reward_eshki > 0:
        await change_balance(
            session,
            user_id,
            mission.reward_eshki,
            reason="mission",
            meta={"mission": mission.code},
        )
    if mission.reward_mmr > 0:
        # Через award_mmr — он сам зеркалит в season MMR активного сезона.
        from app.features.mmr.service import award_mmr
        from app.settings import mmr as mmr_settings

        await award_mmr(
            session,
            player_id=user_id,
            amount=mission.reward_mmr,
            source=mmr_settings.SOURCE_EVENT,
            reason=f"mission:{mission.code}",
        )
