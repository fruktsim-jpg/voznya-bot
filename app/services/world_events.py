"""Сервис записи событий мира (``world_events``).

Единая точка, куда игровые модули пишут «что произошло в мире»: дуэли, кейсы,
ачивки, подарки, сезоны, браки, крупные выигрыши. Это денормализованная
проекция для ленты сайта и AI-нарратора (Тёмный друн), а НЕ замена денежных
леджеров — источник правды по деньгам/состоянию остаётся прежним
(``transactions`` и т.п.).

Принципы:
* Пишет только бот (Model 2).
* ``emit`` зовётся ВНУТРИ той же транзакции, что и само игровое действие —
  тогда событие не может рассинхрониться с исходом. Сам commit делает вызывающий.
* Никогда не роняет геймплей: ошибка проекции логируется и проглатывается
  (``emit_safe``), исход уже зафиксирован своим леджером.
* Идемпотентность по ``(ref_table, ref_id)`` — повторный emit / бэкафилл
  не плодит дубли (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger

logger = get_logger(__name__)

# --- Канонический каталог типов событий (severity по умолчанию) -------------
# severity: 0 болтовня · 1 заметное · 2 крупное · 3 легендарное.
EVENT_CASE_OPEN = "case_open"
EVENT_CASE_JACKPOT = "case_jackpot"
EVENT_CASE_GIFT_DROP = "case_gift_drop"
EVENT_GIFT_PURCHASE = "gift_purchase"
EVENT_GIFT_DELIVERED = "gift_delivered"
EVENT_GIFT_TO_PLAYER = "gift_to_player"
EVENT_CASINO_BIG_WIN = "casino_big_win"
EVENT_TREASURE_FOUND = "treasure_found"
EVENT_ACHIEVEMENT_UNLOCKED = "achievement_unlocked"
EVENT_MARRIAGE_CREATED = "marriage_created"
EVENT_MMR_RANK_UP = "mmr_rank_up"
EVENT_DUEL_WON = "duel_won"
EVENT_SEASON_ENDED = "season_ended"
# Экономические выходки друна (налоговая/подачка из жалости).
EVENT_DRUN_TAX = "drun_tax"
EVENT_DRUN_GRANT = "drun_grant"

DEFAULT_SEVERITY: dict[str, int] = {
    EVENT_CASE_OPEN: 0,
    EVENT_CASE_JACKPOT: 3,
    EVENT_CASE_GIFT_DROP: 2,
    EVENT_GIFT_PURCHASE: 1,
    EVENT_GIFT_DELIVERED: 1,
    EVENT_GIFT_TO_PLAYER: 2,
    EVENT_CASINO_BIG_WIN: 2,
    EVENT_TREASURE_FOUND: 1,
    EVENT_ACHIEVEMENT_UNLOCKED: 1,
    EVENT_MARRIAGE_CREATED: 2,
    EVENT_MMR_RANK_UP: 2,
    EVENT_DUEL_WON: 1,
    EVENT_SEASON_ENDED: 3,
    EVENT_DRUN_TAX: 2,
    EVENT_DRUN_GRANT: 2,
}

_INSERT = text(
    """
    INSERT INTO world_events
        (type, actor_id, target_id, amount, ref_table, ref_id, severity, meta)
    VALUES
        (:type, :actor_id, :target_id, :amount, :ref_table, :ref_id,
         :severity, CAST(:meta AS jsonb))
    ON CONFLICT (ref_table, ref_id) WHERE ref_table IS NOT NULL
    DO NOTHING
    """
)


async def emit(
    session: AsyncSession,
    *,
    type: str,
    actor_id: int | None = None,
    target_id: int | None = None,
    amount: int | None = None,
    ref_table: str | None = None,
    ref_id: int | None = None,
    severity: int | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Записывает событие мира в текущей транзакции (commit — на вызывающем).

    Использует сырой INSERT с ``ON CONFLICT`` для идемпотентности по
    ``(ref_table, ref_id)``. ``meta`` сериализуется в JSON-строку и кастуется
    в jsonb на стороне БД.

    При ``severity >= 2`` дополнительно публикует ``NOTIFY world_events`` с
    компактной JSON-нагрузкой. Это позволяет друну (и любым другим
    подписчикам) реагировать на крупные события в РЕАЛЬНОМ ВРЕМЕНИ, а не
    ждать следующего опросного тика (раньше задержка была 7мин-6ч). NOTIFY
    идёт в той же транзакции, поэтому слушатель никогда не увидит «призрак»
    события, которое в итоге было откатано.
    """
    import json

    sev = severity if severity is not None else DEFAULT_SEVERITY.get(type, 0)
    await session.execute(
        _INSERT,
        {
            "type": type,
            "actor_id": actor_id,
            "target_id": target_id,
            "amount": amount,
            "ref_table": ref_table,
            "ref_id": ref_id,
            "severity": sev,
            "meta": json.dumps(meta or {}),
        },
    )

    # Pub/sub: только для значимых событий, чтобы не захлёбывать слушателя
    # рутиной (открытие кейса с severity=0 — шум). Полезная нагрузка маленькая:
    # тип, severity, актор/жертва, сумма — этого хватит подписчику решить,
    # нужно ли поднимать тяжёлый контекст.
    if sev >= 2:
        try:
            payload = json.dumps(
                {
                    "type": type,
                    "severity": sev,
                    "actor_id": actor_id,
                    "target_id": target_id,
                    "amount": amount,
                    "ref_table": ref_table,
                    "ref_id": ref_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            # pg_notify(channel, payload) — параметризованный вариант NOTIFY,
            # безопасный для произвольных строк. Postgres ограничивает payload
            # ~8000 байт; наш меньше 300 — с большим запасом.
            await session.execute(
                text("SELECT pg_notify('world_events', :p)"), {"p": payload},
            )
        except Exception:  # noqa: BLE001
            # NOTIFY не критичен: при сбое подписчики просто отработают по
            # своему опросному тику. Лог в WARNING, чтобы заметить деградацию.
            logger.warning("world_events pg_notify failed", exc_info=True)


async def emit_safe(session: AsyncSession, **kwargs: Any) -> None:
    """Как :func:`emit`, но не бросает исключений.

    Для мест, где исход уже зафиксирован своим леджером и сбой проекции не
    должен ломать геймплей. Ошибку логируем и проглатываем.
    """
    try:
        await emit(session, **kwargs)
    except Exception:  # noqa: BLE001
        logger.warning(
            "world_events emit failed (type=%s)",
            kwargs.get("type"),
            exc_info=True,
        )
