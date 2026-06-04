"""Логика системы достижений.

Достижения проверяются после действий, влияющих на статистику. Каждое
достижение открывается один раз (гарантируется первичным ключом таблицы
``user_achievements``) и может выдавать бонусные ешки.

Виды достижений:
* метрические — открываются при достижении порога метрики;
* событийные (metric="event") — выдаются точечно из кода (джекпот, быстрый
  клад, возвращение и т.п.) через :func:`award_specific`;
* «all» — открывается, когда открыты все основные достижения.

Награда увеличивает total_earned, что может открыть следующее достижение,
поэтому метрическая проверка идёт в цикле до стабилизации.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.core.utils import mention as mention_html
from app.core.utils import progress_bar
from app.models import Marriage, User, UserAchievement
from app.services.economy import change_balance
from app.settings import texts
from app.settings.achievements import (
    ACHIEVEMENTS,
    ACHIEVEMENTS_BY_CODE,
    CATEGORY_ORDER,
    CORE_ACHIEVEMENT_CODES,
    METRIC_ALL,
    METRIC_EVENT,
    SECRET_CATEGORY,
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
        "pidor_count": user.pidor_count,
        "max_farm_streak": user.max_farm_streak,
        "max_casino_loss": user.max_casino_loss,
        "casino_loss_streak": user.casino_loss_streak,
        "duel_loss_streak": user.duel_loss_streak,
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


async def _grant(session: AsyncSession, user_id: int, ach: Achievement) -> bool:
    """Открывает достижение и выдаёт награду. True — если открыто сейчас."""
    if not await _try_unlock(session, user_id, ach.code):
        return False
    if ach.reward:
        await change_balance(
            session, user_id, ach.reward, "achievement", {"code": ach.code}
        )
    return True


async def check_and_award(session: AsyncSession, user_id: int) -> list[Achievement]:
    """Проверяет и открывает все доступные метрические достижения.

    Возвращает список достижений, открытых в рамках этого вызова.
    """
    newly: list[Achievement] = []

    for _ in range(len(ACHIEVEMENTS) + 1):
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            break
        stats = await _gather_stats(session, user)
        unlocked = await get_unlocked_codes(session, user_id)
        progressed = False

        for ach in ACHIEVEMENTS:
            if ach.metric in (METRIC_ALL, METRIC_EVENT) or ach.code in unlocked:
                continue
            if stats.get(ach.metric, 0) >= ach.threshold:
                if await _grant(session, user_id, ach):
                    newly.append(ach)
                    progressed = True

        # Достижения типа «all» (например, «Меллстрой Возни»).
        for ach in ACHIEVEMENTS:
            if ach.metric != METRIC_ALL or ach.code in unlocked:
                continue
            fresh = await get_unlocked_codes(session, user_id)
            if CORE_ACHIEVEMENT_CODES.issubset(fresh):
                if await _grant(session, user_id, ach):
                    newly.append(ach)
                    progressed = True

        if not progressed:
            break

    return newly


async def award_specific(
    session: AsyncSession, user_id: int, code: str
) -> Achievement | None:
    """Открывает конкретное (событийное) достижение, если оно ещё закрыто."""
    ach = ACHIEVEMENTS_BY_CODE.get(code)
    if ach is None:
        return None
    if await _grant(session, user_id, ach):
        return ach
    return None


def format_unlock_notification(
    user_id: int, name: str | None, username: str | None, newly: list[Achievement]
) -> str | None:
    """Формирует карточку об открытых достижениях (или None, если их нет)."""
    if not newly:
        return None
    who = mention_html(user_id, name, username)
    lines = [
        texts.ACH_UNLOCK_ROW.format(
            label=ach.label,
            reward=texts.ACH_REWARD.format(reward=money(ach.reward)) if ach.reward else "",
        )
        for ach in newly
    ]
    return texts.ACH_UNLOCK.format(who=who, lines="\n".join(lines))


async def check_award_and_notify(
    answerable,
    session: AsyncSession,
    user_id: int,
    name: str | None,
    username: str | None,
) -> list[Achievement]:
    """Проверяет метрические достижения и шлёт уведомление о новых."""
    newly = await check_and_award(session, user_id)
    text = format_unlock_notification(user_id, name, username, newly)
    if text:
        await answerable.answer(text)
    return newly


async def notify_specific(
    answerable,
    session: AsyncSession,
    user_id: int,
    name: str | None,
    username: str | None,
    code: str,
) -> None:
    """Выдаёт событийное достижение и шлёт уведомление, если оно открылось."""
    ach = await award_specific(session, user_id, code)
    if ach is not None:
        text = format_unlock_notification(user_id, name, username, [ach])
        if text:
            await answerable.answer(text)


async def render_achievements(session: AsyncSession, user_id: int) -> str:
    """Формирует компактную карточку достижений, сгруппированную по категориям."""
    unlocked = await get_unlocked_codes(session, user_id)
    total = len(ACHIEVEMENTS)
    opened = sum(1 for a in ACHIEVEMENTS if a.code in unlocked)

    parts = [texts.ACH_HEADER.format(opened=opened, total=total, bar=progress_bar(opened / total))]

    for category, label in CATEGORY_ORDER:
        items = [a for a in ACHIEVEMENTS if a.category == category]
        if not items:
            continue
        parts.append(f"\n{texts.DIV}\n{label}")
        for a in items:
            mark = "✅" if a.code in unlocked else "🔒"
            parts.append(f"{mark} {a.label}")

    # Секретные: открытые показываем, закрытые — только счётчиком.
    secrets = [a for a in ACHIEVEMENTS if a.category == SECRET_CATEGORY]
    if secrets:
        opened_secrets = [a for a in secrets if a.code in unlocked]
        locked_count = len(secrets) - len(opened_secrets)
        parts.append(f"\n{texts.DIV}\n🤫 Секретные")
        for a in opened_secrets:
            parts.append(f"✅ {a.label}")
        if locked_count:
            parts.append(f"🔒 ??? × {locked_count}")

    return "\n".join(parts)
