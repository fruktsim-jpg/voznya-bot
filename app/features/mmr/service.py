"""Бизнес-логика рейтинга MMR: начисление и определение «редкости» ачивок.

Главная точка — :func:`award_mmr`: единый помощник, через который ВСЕ игровые
модули начисляют/списывают рейтинг. Он пишет строку в журнал ``mmr_entries``
(источник правды) и не делает commit — его выполнит middleware после успешной
обработки апдейта (как в остальном коде).

MMR изолирован от ешек/репутации/сообщений/магазина/инвентаря/Combot. Здесь
нет никаких изменений баланса или иных систем — только запись в свой журнал.
"""

from __future__ import annotations

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
) -> None:
    """Начисляет (или списывает при amount<0) MMR игроку и логирует это.

    Единая точка входа для всех источников рейтинга. ``amount == 0``
    игнорируется (пустых записей не пишем). Commit делает вызывающий код.
    """
    if amount == 0:
        return
    await mmr_repo.add_entry(
        session,
        player_id=player_id,
        amount=amount,
        source=source,
        reason=reason,
    )


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


