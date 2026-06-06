"""События открытия кейсов — точки подключения будущих достижений.

Заложено заранее (Cases V1), хотя сами ачивки появятся позже. Это ЕДИНСТВЕННОЕ
место, куда будущая система достижений подключит свои проверки по кейсам — не
размазывая их по хендлерам. Сейчас функция вычисляет агрегаты и вызывает
(пока пустые) хуки; при появлении ачивок достаточно дополнить хуки, не трогая
рантайм открытия.

Заложенные будущие достижения:
* открыто кейсов (всего) — порог по количеству открытий;
* открыто редких кейсов — открытия кейсов редкости rare+;
* получено легендарных наград — выпал предмет редкости legendary;
* суммарно заработано из кейсов — сумма ешек-наград.

Данные для всех этих ачивок уже полностью восстановимы из ``case_openings``
(+ ``inventory_items.rarity`` для редкости), поэтому пороги можно считать здесь
без новой схемы.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CaseOpenEvent:
    """Факт открытия кейса — вход для будущих проверок достижений."""

    user_id: int
    case_item_code: str
    reward_kind: str
    reward_item_code: str | None
    reward_rarity: str | None
    amount: int | None
    is_jackpot: bool
    total_openings: int  # сколько всего кейсов открыл игрок (после этого)


async def emit_case_opened(event: CaseOpenEvent) -> None:
    """Единая точка событий открытия кейса.

    Сейчас только логирует — будущая система достижений подключит сюда проверки:
    «открыто кейсов», «редкие кейсы», «легендарные награды», «заработано из
    кейсов». Намеренно не бросает исключений: проблемы с ачивками не должны
    ломать уже зафиксированное открытие (оно в отдельной транзакции хендлера).
    """
    # Задел: здесь будущая система достижений вызовет свои проверки, например:
    #   await achievements.check_threshold(..., "cases_opened", event.total_openings)
    #   if event.reward_rarity == "legendary": await achievements.grant(..., "legendary_drop")
    logger.info(
        "case_opened user=%s case=%s kind=%s rarity=%s amount=%s jackpot=%s total=%s",
        event.user_id,
        event.case_item_code,
        event.reward_kind,
        event.reward_rarity,
        event.amount,
        event.is_jackpot,
        event.total_openings,
    )
