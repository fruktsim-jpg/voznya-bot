"""Бизнес-логика рейтинга MMR: начисление, повышение ранга и «редкость» ачивок.

Главная точка — :func:`award_mmr`: единый помощник, через который ВСЕ игровые
модули начисляют/списывают рейтинг. Он пишет строку в журнал ``mmr_entries``
(источник правды) и не делает commit — его выполнит middleware после успешной
обработки апдейта (как в остальном коде).

MMR изолирован от ешек/репутации/сообщений/магазина/инвентаря/Combot. Здесь
нет никаких изменений баланса или иных систем — только запись в свой журнал.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import mmr as mmr_repo
from app.settings import mmr as mmr_settings
from app.settings.achievements import SECRET_CATEGORY, Achievement


async def award_mmr(
    session: AsyncSession,
    *,
    player_id: int,
    amount: int,
    source: str,
    reason: str | None = None,
) -> mmr_settings.Rank | None:
    """Начисляет (или списывает при amount<0) MMR игроку и логирует это.

    Единая точка входа для всех источников рейтинга. ``amount == 0``
    игнорируется (пустых записей не пишем). Commit делает вызывающий код.

    Возвращает новый ранг, если именно это начисление подняло игрока на
    следующую ступень — чтобы вызывающий код мог объявить повышение. Если
    ранг не изменился или это списание (amount<0) — возвращает ``None``.
    """
    if amount == 0:
        return None
    # Рейтинг ДО записи: новая строка ещё не добавлена, поэтому SUM не включает
    # текущее начисление. Итог получаем арифметикой, без второго запроса.
    before = await mmr_repo.get_mmr(session, player_id)
    await mmr_repo.add_entry(
        session,
        player_id=player_id,
        amount=amount,
        source=source,
        reason=reason,
    )
    # Зеркалим то же изменение в СЕЗОННЫЙ MMR, если сейчас идёт активный сезон.
    # Lifetime MMR (выше) копится всегда; season MMR — только в окне сезона и
    # сбрасывается между сезонами. Изоляция импорта во избежание циклов.
    await _mirror_to_season(
        session, player_id=player_id, amount=amount, source=source, reason=reason
    )
    if amount < 0:
        return None
    return _rankup_between(before, before + amount)


async def _mirror_to_season(
    session: AsyncSession,
    *,
    player_id: int,
    amount: int,
    source: str,
    reason: str | None,
) -> None:
    """Дублирует начисление в сезонный MMR, если есть активный сезон."""
    from app.repositories import season as season_repo

    active = await season_repo.get_active_season(session)
    if active is None:
        return
    await season_repo.add_season_mmr(
        session,
        season_id=active.id,
        player_id=player_id,
        amount=amount,
        source=source,
        reason=reason,
    )



def _rankup_between(before: int, after: int) -> mmr_settings.Rank | None:
    """Возвращает новый ранг, если он вырос на отрезке before → after."""
    old_rank = mmr_settings.get_rank(before)
    new_rank = mmr_settings.get_rank(after)
    if new_rank.min_mmr > old_rank.min_mmr:
        return new_rank
    return None


async def detect_rankup(
    session: AsyncSession, user_id: int, mmr_before: int
) -> mmr_settings.Rank | None:
    """Ловит повышение ранга по итогу, зная рейтинг ДО серии начислений.

    Нужно там, где MMR мог начислиться «внутри» другого процесса (например,
    награда за достижение проходит через сервис достижений), и поймать переход
    отдельным сравнением проще, чем тащить ранг сквозь все слои.
    """
    after = await mmr_repo.get_mmr(session, user_id)
    return _rankup_between(mmr_before, after)


def format_rankup(user_mention: str, rank: mmr_settings.Rank) -> str:
    """Случайная фраза о повышении ранга (пул из настроек MMR)."""
    return random.choice(mmr_settings.MMR_RANKUP_VARIANTS).format(
        mention=user_mention, rank_emoji=rank.emoji, rank_name=rank.name
    )


async def announce_rankup_if_any(
    answerable,
    session: AsyncSession,
    user_id: int,
    user_mention: str,
    mmr_before: int,
) -> None:
    """Сравнивает ранг с ``mmr_before`` и шлёт сообщение, если игрок поднялся.

    Вызывается в конце игрового хендлера: ``mmr_before`` нужно снять ДО любых
    начислений MMR в этом апдейте (и геймплейных, и за достижения), чтобы
    поймать переход целиком одним уведомлением.
    """
    rank = await detect_rankup(session, user_id, mmr_before)
    if rank is not None:
        await answerable.answer(format_rankup(user_mention, rank))


def mmr_for_achievement(ach: Achievement) -> int:
    """Сколько MMR даёт ачивка в зависимости от её «редкости».

    Редкость выводится из каталога достижений (отдельного поля rarity нет):

    * легендарная — категория ``legend``;
    * редкая — секретная (``hidden=True`` / категория ``secret``);
    * обычная — все прочие.
    """
    if ach.category == "legend":
        return mmr_settings.MMR_ACHIEVEMENT_LEGENDARY
    if ach.hidden or ach.category == SECRET_CATEGORY:
        return mmr_settings.MMR_ACHIEVEMENT_RARE
    return mmr_settings.MMR_ACHIEVEMENT
