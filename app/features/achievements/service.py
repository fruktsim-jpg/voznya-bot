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
from app.models import (
    CaseOpening,
    GiftTransaction,
    Inventory,
    LoginStreak,
    Marriage,
    User,
    UserAchievement,
)
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
    """Собирает значения метрик для проверки достижений.

    Большинство метрик читаются прямо с ``users`` (денормализованные счётчики).
    Кейсы/подарки/коллекция считаются агрегатами по существующим журналам/
    таблицам (case_openings, gift_transactions, inventory) — новых таблиц/
    счётчиков НЕ заводим. Серия входов берётся из login_streaks (best_streak).
    """
    marriages_count = await session.scalar(
        select(func.count())
        .select_from(Marriage)
        .where(or_(Marriage.user_id_1 == user.user_id, Marriage.user_id_2 == user.user_id))
    )

    # Кейсы: число открытий из append-only журнала case_openings.
    cases_opened = await session.scalar(
        select(func.count())
        .select_from(CaseOpening)
        .where(CaseOpening.user_id == user.user_id)
    )

    # Подарки: сколько получено (предметы и tg_gift; валютные подарки не в счёт).
    gifts_received = await session.scalar(
        select(func.count())
        .select_from(GiftTransaction)
        .where(
            GiftTransaction.recipient_user_id == user.user_id,
            GiftTransaction.kind.in_(("item", "tg_gift")),
            GiftTransaction.status == "completed",
        )
    )

    # Коллекция: число РАЗНЫХ предметов в инвентаре (по item_code).
    distinct_items = await session.scalar(
        select(func.count(func.distinct(Inventory.item_code)))
        .where(Inventory.user_id == user.user_id, Inventory.quantity > 0)
    )

    # Серия входов: лучший достигнутый стрик (login_streaks.best_streak).
    best_login_streak = await session.scalar(
        select(LoginStreak.best_streak).where(
            LoginStreak.player_id == user.user_id
        )
    )

    return {
        "total_earned": user.total_earned,
        "total_spent": user.total_spent,
        "messages_count": user.messages_count,
        "season_mmr": user.season_mmr,
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
        "cases_opened": int(cases_opened or 0),
        "gifts_received": int(gifts_received or 0),
        "distinct_items": int(distinct_items or 0),
        "login_streak": int(best_login_streak or 0),
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
    # MMR за ачивку — отдельный игровой рейтинг (зависит от редкости ачивки).
    # Изолирован от ешек: начисляется всегда, даже если ачивка без награды.
    from app.features.mmr.service import award_mmr, mmr_for_achievement
    from app.settings import mmr as mmr_settings

    await award_mmr(
        session,
        player_id=user_id,
        amount=mmr_for_achievement(ach),
        source=mmr_settings.SOURCE_ACHIEVEMENT,
        reason=ach.code,
    )

    # Событие мира: открыто достижение. Та же транзакция.
    from app.services import world_events

    await world_events.emit_safe(
        session,
        type=world_events.EVENT_ACHIEVEMENT_UNLOCKED,
        actor_id=user_id,
        meta={
            "code": ach.code,
            "name": getattr(ach, "title", None) or getattr(ach, "name", None),
            "reward": ach.reward,
        },
    )
    return True



async def check_and_award(session: AsyncSession, user_id: int) -> list[Achievement]:
    """Проверяет и открывает все доступные метрические достижения.

    Возвращает список достижений, открытых в рамках этого вызова.
    """
    newly: list[Achievement] = []

    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        return newly

    # Горячий путь (вызывается на каждое игровое действие): тяжёлые запросы
    # делаем ОДИН раз до цикла. 5 агрегатов из _gather_stats не зависят от
    # выдачи достижений и не меняются в цикле; меняется только total_earned —
    # его держим на in-memory user-объекте (change_balance мутирует ту же
    # сущность в той же сессии). Открытые коды держим в set и обновляем по мере
    # выдачи вместо повторных SELECT'ов.
    stats = await _gather_stats(session, user)
    unlocked = await get_unlocked_codes(session, user_id)

    for _ in range(len(ACHIEVEMENTS) + 1):
        progressed = False

        for ach in ACHIEVEMENTS:
            if ach.metric in (METRIC_ALL, METRIC_EVENT) or ach.code in unlocked:
                continue
            if stats.get(ach.metric, 0) >= ach.threshold:
                if await _grant(session, user_id, ach):
                    newly.append(ach)
                    unlocked.add(ach.code)
                    progressed = True

        # Достижения типа «all» (например, «Меллстрой Возни»). Используем тот же
        # in-memory набор открытых кодов (он уже включает выданные в этом проходе
        # метрические достижения — как раньше делал повторный SELECT).
        for ach in ACHIEVEMENTS:
            if ach.metric != METRIC_ALL or ach.code in unlocked:
                continue
            if CORE_ACHIEVEMENT_CODES.issubset(unlocked):
                if await _grant(session, user_id, ach):
                    newly.append(ach)
                    unlocked.add(ach.code)
                    progressed = True

        if not progressed:
            break

        # Награды за этот проход могли увеличить total_earned (на том же
        # user-объекте). Обновляем только его — остальные метрики неизменны.
        # Эквивалентно пере-сбору stats в начале следующего прохода.
        stats["total_earned"] = user.total_earned

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


def _reward_suffix(ach: Achievement) -> str:
    return texts.ACH_REWARD.format(reward=money(ach.reward)) if ach.reward else ""


def format_unlock_notification(
    user_id: int, name: str | None, username: str | None, newly: list[Achievement]
) -> str | None:
    """Формирует короткое уведомление об открытых достижениях (2–3 строки)."""
    if not newly:
        return None
    
    from app.core.utils import mention
    user_mention = mention(user_id, name, username)
    
    if len(newly) == 1:
        ach = newly[0]
        reward = money(ach.reward) if ach.reward else texts.ACH_UNLOCK_NO_REWARD
        return texts.ACH_UNLOCK_ONE.format(
            mention=user_mention,
            name=ach.name,
            description=ach.description,
            reward=reward,
        )
    lines = "\n".join(
        texts.ACH_UNLOCK_ROW.format(
            name=ach.name,
            description=ach.description,
            reward=_reward_suffix(ach),
        )
        for ach in newly
    )
    return texts.ACH_UNLOCK_MANY.format(mention=user_mention, lines=lines)



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


async def render_achievements_compact(
    session: AsyncSession, user_id: int, first_name: str, username: str | None = None
) -> str:
    """Формирует компактный список ТОЛЬКО открытых достижений."""
    from app.core.utils import mention
    
    unlocked = await get_unlocked_codes(session, user_id)
    total = len(ACHIEVEMENTS)
    opened = sum(1 for a in ACHIEVEMENTS if a.code in unlocked)

    parts = [texts.ACH_HEADER.format(
        mention=mention(user_id, first_name, username),
        opened=opened,
        total=total
    )]
    
    if opened == 0:
        parts.append("\nПока ничего не открыто.")
    else:
        # Только открытые достижения (без категорий, без секретных)
        for a in ACHIEVEMENTS:
            if a.code in unlocked and a.category != SECRET_CATEGORY:
                parts.append(texts.ACH_OPENED_ROW.format(label=a.label))

    return "\n".join(parts)


async def render_achievements_full(
    session: AsyncSession, user_id: int, first_name: str, username: str | None = None
) -> str:
    """Формирует полный список достижений с категориями и замками."""
    from app.core.utils import mention
    
    unlocked = await get_unlocked_codes(session, user_id)
    total = len(ACHIEVEMENTS)
    opened = sum(1 for a in ACHIEVEMENTS if a.code in unlocked)

    parts = [texts.ACH_HEADER.format(
        mention=mention(user_id, first_name, username),
        opened=opened,
        total=total
    )]

    for category, label in CATEGORY_ORDER:
        items = [a for a in ACHIEVEMENTS if a.category == category]
        if not items:
            continue
        parts.append(f"\n{label}")
        for a in items:
            row = texts.ACH_OPENED_ROW if a.code in unlocked else texts.ACH_LOCKED_ROW
            parts.append(row.format(label=a.label))

    # Секретные: открытые показываем, закрытые — только счётчиком.
    secrets = [a for a in ACHIEVEMENTS if a.category == SECRET_CATEGORY]
    if secrets:
        opened_secrets = [a for a in secrets if a.code in unlocked]
        locked_count = len(secrets) - len(opened_secrets)
        parts.append("\n🤫 Секретные")
        for a in opened_secrets:
            parts.append(texts.ACH_OPENED_ROW.format(label=a.label))
        if locked_count:
            parts.append(f"🔒 ??? × {locked_count}")

    return "\n".join(parts)


# Алиас для обратной совместимости
async def render_achievements(session: AsyncSession, user_id: int) -> str:
    """Формирует компактный список достижений (по умолчанию)."""
    return await render_achievements_compact(session, user_id)
