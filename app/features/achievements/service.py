"""Логика системы достижений.

Достижения проверяются после действий, влияющих на статистику. Каждое
достижение открывается один раз (гарантируется первичным ключом таблицы
``user_achievements``) и может выдавать бонусные ешки.

Награда за достижение увеличивает ``total_earned``, что может открыть ещё одно
достижение — поэтому проверка идёт в цикле до стабилизации (без полумер).
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.core.utils import mention as mention_html
from app.models import Marriage, User, UserAchievement
from app.services.economy import change_balance
from app.settings.achievements import (
    ACHIEVEMENTS,
    ACHIEVEMENTS_BY_CODE,
    METRIC_ALL,
    Achievement,
)


async def _gather_stats(session: AsyncSession, user: User) -> dict[str, int]:
    """Собирает значения метрик для проверки достижений."""
    marriages_count = await session.scalar(
        select(func.count())
        .select_from(Marriage)
        .where(or_(Marriage.user_id_1 == user.user_id, Marriage.user_id_2 == user.user_id))
    )
    return {
        "total_earned": user.total_earned,
        "farm_success_count": user.farm_success_count,
        "casino_games_count": user.casino_games_count,
        "duels_won": user.duels_won,
        "treasures_found": user.treasures_found,
        "marriages_count": int(marriages_count or 0),
    }


async def get_unlocked_codes(session: AsyncSession, user_id: int) -> set[str]:
    """Возвращает коды уже открытых достижений пользователя."""
    result = await session.execute(
        select(UserAchievement.code).where(UserAchievement.user_id == user_id)
    )
    return {row[0] for row in result.all()}


async def _try_unlock(session: AsyncSession, user_id: int, code: str) -> bool:
    """Пытается открыть достижение. Возвращает True, если открыто именно сейчас."""
    stmt = (
        pg_insert(UserAchievement)
        .values(user_id=user_id, code=code)
        .on_conflict_do_nothing(index_elements=["user_id", "code"])
        .returning(UserAchievement.code)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def check_and_award(session: AsyncSession, user_id: int) -> list[Achievement]:
    """Проверяет и открывает все доступные достижения пользователя.

    Возвращает список достижений, открытых в рамках этого вызова.
    """
    newly: list[Achievement] = []
    legend = next((a for a in ACHIEVEMENTS if a.metric == METRIC_ALL), None)
    non_legend_codes = {a.code for a in ACHIEVEMENTS if a.metric != METRIC_ALL}

    # Цикл ловит каскад: награда за достижение может открыть следующее.
    for _ in range(len(ACHIEVEMENTS) + 1):
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            break
        stats = await _gather_stats(session, user)
        unlocked = await get_unlocked_codes(session, user_id)
        progressed = False

        for ach in ACHIEVEMENTS:
            if ach.metric == METRIC_ALL or ach.code in unlocked:
                continue
            if stats.get(ach.metric, 0) >= ach.threshold:
                if await _try_unlock(session, user_id, ach.code):
                    newly.append(ach)
                    progressed = True
                    if ach.reward:
                        await change_balance(
                            session, user_id, ach.reward, "achievement", {"code": ach.code}
                        )

        # «Легенда Возни» — когда открыты все прочие достижения.
        if legend is not None and legend.code not in unlocked:
            fresh_unlocked = await get_unlocked_codes(session, user_id)
            if non_legend_codes.issubset(fresh_unlocked):
                if await _try_unlock(session, user_id, legend.code):
                    newly.append(legend)
                    progressed = True
                    if legend.reward:
                        await change_balance(
                            session, user_id, legend.reward, "achievement",
                            {"code": legend.code},
                        )

        if not progressed:
            break

    return newly


async def check_award_and_notify(
    answerable,
    session: AsyncSession,
    user_id: int,
    name: str | None,
    username: str | None,
) -> list[Achievement]:
    """Проверяет достижения и, если есть новые, шлёт уведомление в чат.

    ``answerable`` — объект с методом ``answer`` (Message или message внутри
    CallbackQuery).
    """
    newly = await check_and_award(session, user_id)
    text = format_unlock_notification(user_id, name, username, newly)
    if text:
        await answerable.answer(text)
    return newly


def format_unlock_notification(
    user_id: int, name: str | None, username: str | None, newly: list[Achievement]
) -> str | None:
    """Формирует сообщение об открытых достижениях (или None, если их нет)."""
    if not newly:
        return None
    who = mention_html(user_id, name, username)
    lines = [f"🎉 {who} открывает достижение:"]
    for ach in newly:
        suffix = f" (+{money(ach.reward)})" if ach.reward else ""
        lines.append(f"{ach.label}{suffix}")
    return "\n".join(lines)


async def render_achievements(session: AsyncSession, user_id: int) -> str:
    """Формирует текст для команды /ачивки."""
    unlocked = await get_unlocked_codes(session, user_id)
    total = len(ACHIEVEMENTS)
    opened = len(unlocked & set(ACHIEVEMENTS_BY_CODE))

    lines = [f"🏅 <b>Достижения</b> — открыто {opened} из {total}\n"]
    for ach in ACHIEVEMENTS:
        if ach.code in unlocked:
            mark = "✅"
            reward = ""
        else:
            mark = "🔒"
            reward = f" (награда +{money(ach.reward)})" if ach.reward else ""
        lines.append(f"{mark} {ach.label} — {ach.description}{reward}")
    return "\n".join(lines)
