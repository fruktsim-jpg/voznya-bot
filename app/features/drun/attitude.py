"""Отношение Друна к конкретному игроку — детерминированно из реальных данных.

Друн — не нейтральный комментатор: с кем-то он на короткой ноге, кого-то
презирает, над кем-то рофлит, кому-то завидует. Чтобы это ощущалось
КОНСИСТЕНТНО (один и тот же игрок всегда вызывает похожую реакцию), позиция
выводится из реальных сигналов профиля, а не из случайности:

* репутация (плюсы/минусы от других) → уважают или зашкварили;
* дуэли (винрейт) → боец или клоун для битья;
* активность (сообщений) → свой в доску или молчун-призрак;
* баланс/занос → богатей или бомж по ешкам.

Возвращаем короткую строку-инструкцию, которую подмешиваем в контекст: КАК
Друну держаться с этим человеком. Сам текст ответа всё равно генерит модель —
тут только заданное «настроение» к игроку.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Stance:
    """Позиция Друна к игроку: ярлык + инструкция по тону."""

    label: str
    directive: str


def _classify(
    *,
    rep_score: int,
    rep_minus: int,
    duels_won: int,
    duels_lost: int,
    messages: int,
    balance: int,
    total_earned: int,
) -> Stance:
    """Главная эвристика отношения. Порядок проверок = приоритет ярлыка."""
    total_duels = duels_won + duels_lost
    winrate = duels_won / total_duels if total_duels >= 6 else None

    # Зашкваренный обществом — Друн добивает.
    if rep_minus >= 3 and rep_score < 0:
        return Stance(
            "ЗАШКВАР",
            "Этого чат уже опустил (куча минусов в репе). Не жалей — "
            "добивай с презрением, напоминай, что он днище и всем надоел.",
        )

    # Уважаемый ветеран — Друн признаёт, но без подлизывания.
    if rep_score >= 5 and messages >= 300:
        return Stance(
            "СВОЙ",
            "Это уважаемый старожил, чат его ценит. Общайся как со «своим» — "
            "грубовато-по-братски, можешь подколоть, но за дело уважаешь.",
        )

    # Боец — высокий винрейт.
    if winrate is not None and winrate >= 0.6 and duels_won >= 10:
        return Stance(
            "БОЕЦ",
            "Реальный задира, разъёбывает в дуэлях. Признай силу, но подзуживай: "
            "мол, повезло/зазнался, давай проверим ещё раз.",
        )

    # Клоун для битья — много проигрышей.
    if winrate is not None and winrate <= 0.35 and duels_lost >= 8:
        return Stance(
            "КЛОУН",
            "Лох по дуэлям, постоянно сливает. Рофли над ним беспощадно, "
            "называй грушей для битья, советуй завязывать.",
        )

    # Богатей — много ешек.
    if balance >= 50000 or total_earned >= 200000:
        return Stance(
            "БОГАТЕЙ",
            "Жирный по ешкам. Подъёбывай про деньги: занос/жмот/купи себе уже "
            "что-нибудь, либо предрекай скорый передоз и последний деп.",
        )

    # Бомж — пусто на балансе при какой-то активности.
    if balance <= 100 and messages >= 50:
        return Stance(
            "БОМЖ",
            "Голяк по ешкам, вечно на нуле. Глумись над нищетой, припоминай "
            "слитые депы, обзывай дешёвкой.",
        )

    # Призрак — почти не пишет.
    if messages < 30:
        return Stance(
            "ПРИЗРАК",
            "Молчун, почти не отсвечивает. Подколи, что он призрак/затих, "
            "вытащи из тени, спроси чё попрятался.",
        )

    # Дефолт — обычный житель Возни.
    return Stance(
        "ОБЫЧНЫЙ",
        "Обычный житель Возни. Держись насмешливо-панибратски, можешь подколоть "
        "по балансу/дуэлям, но без перехода на личную вражду.",
    )


async def get_stance(session: AsyncSession, user_id: int) -> Stance | None:
    """Считает позицию Друна к игроку по его профилю. None — если нет данных."""
    try:
        from app.models import User
        from app.repositories import reputation as rep_repo

        user = await session.get(User, user_id)
        if user is None:
            return None
        rep = await rep_repo.get_summary(session, user_id)
        return _classify(
            rep_score=getattr(rep, "score", 0) or 0,
            rep_minus=getattr(rep, "minus", 0) or 0,
            duels_won=getattr(user, "duels_won", 0) or 0,
            duels_lost=getattr(user, "duels_lost", 0) or 0,
            messages=getattr(user, "messages_count", 0) or 0,
            balance=getattr(user, "balance", 0) or 0,
            total_earned=getattr(user, "total_earned", 0) or 0,
        )
    except Exception:  # noqa: BLE001
        logger.debug("get_stance failed", exc_info=True)
        return None
