"""Инструменты владельца: реальные действия друна над миром Возни.

Владелец пишет в чате обычным языком — «друн дай всем кто писал за час 100
ешек», «друн сбрось всем кд на ферму», «друн разыграй 5000 среди активных» — а
LLM-планировщик (``agent.py``) превращает это в вызов одного из инструментов
отсюда. Здесь только исполнение с жёсткими предохранителями и аудитом.

ГЕЙТ: инструменты вызываются ТОЛЬКО для пользователей из ``ADMIN_IDS``
(эффективный owner). Это проверяет ``agent.py`` ДО планирования; сами функции
тоже не доверяют вводу и клампят масштабы.

Каждый инструмент:
* ограничен предохранителями (лимиты сумм, размер аудитории);
* пишет ``world_events`` и ``audit_log`` (прозрачность и возможность отката);
* возвращает ``ToolResult`` с человекочитаемым итогом для ответа друна.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.money import money
from app.core.utils import now_utc
from app.models import AiMessage, User
from app.repositories import users as users_repo
from app.services import economy

logger = get_logger(__name__)

# --- Предохранители (жёсткие потолки, чтобы случайно не сжечь экономику) ------
MAX_GRANT_PER_USER = 100_000      # максимум ешек одному за одну операцию
MAX_BULK_USERS = 500              # максимум получателей в одной bulk-операции
MAX_GIVEAWAY_POOL = 5_000_000     # максимум призового фонда розыгрыша
REASON_OWNER = "owner_drun"       # reason для transactions/аудита


@dataclass
class ToolResult:
    """Итог исполнения инструмента."""

    ok: bool
    summary: str = ""                 # короткий человекочитаемый итог
    affected: int = 0                 # сколько игроков затронуто
    error: str = ""
    meta: dict = field(default_factory=dict)


async def _audit(
    session: AsyncSession, actor_id: int, action: str, target_id: int | None,
    reason: str, meta: dict,
) -> None:
    """Пишет строку аудита (мягко: сбой аудита не валит операцию)."""
    try:
        from app.repositories import moderation as mod_repo

        await mod_repo.write_audit(
            session, actor_user_id=actor_id, actor_role="owner",
            action=action, target_user_id=target_id, reason=reason, meta=meta,
        )
    except Exception:  # noqa: BLE001
        logger.debug("tool audit failed", exc_info=True)


async def _debit_clamped_to_balance(
    session: AsyncSession, user_id: int, debit: int, reason: str, meta: dict,
) -> tuple[int, int]:
    """Снимает у игрока не больше, чем у него есть (как admin «забрать»).

    ``debit`` — положительная величина к снятию. Возвращает (реально снято,
    новый баланс). Не уводит в минус и НЕ падает на нехватке средств: это
    осознанная семантика снятия — забираем сколько есть. Возврат (0, balance),
    если снимать нечего.
    """
    user = await users_repo.get_user(session, user_id)
    have = int(getattr(user, "balance", 0) or 0) if user else 0
    take = min(int(debit), max(have, 0))
    if take <= 0:
        return 0, have
    updated = await economy.change_balance(
        session, user_id, -take, reason, meta, allow_negative=False
    )
    return take, int(getattr(updated, "balance", 0) or 0)


async def _audience_recent_chat(
    session: AsyncSession, *, minutes: int, exclude: set[int] | None = None
) -> list[int]:
    """Игроки, писавшие живые реплики за последние ``minutes`` минут."""
    since = now_utc() - timedelta(minutes=max(1, minutes))
    rows = (
        await session.execute(
            select(AiMessage.user_id)
            .where(AiMessage.role == "chat")
            .where(AiMessage.user_id.is_not(None))
            .where(AiMessage.created_at >= since)
            .group_by(AiMessage.user_id)
        )
    ).scalars().all()
    ex = exclude or set()
    return [uid for uid in rows if uid not in ex]


async def _audience_active(
    session: AsyncSession, *, days: int, exclude: set[int] | None = None
) -> list[int]:
    """Игроки, активные за последние ``days`` дней (по last_active_at)."""
    from app.repositories import users as users_repo

    return await users_repo.get_active_user_ids(session, days, exclude=exclude)


async def resolve_audience(
    session: AsyncSession, *, scope: str, minutes: int = 60, days: int = 7,
) -> list[int]:
    """Возвращает список user_id целевой аудитории по описанию ``scope``.

    scope: ``recent`` (писали за ``minutes`` мин) / ``active`` (активны за
    ``days`` дней) / ``all`` (все игроки с балансом-историей).
    """
    if scope == "recent":
        ids = await _audience_recent_chat(session, minutes=minutes)
    elif scope == "all":
        ids = (await session.execute(select(User.user_id))).scalars().all()
        ids = list(ids)
    else:  # active
        ids = await _audience_active(session, days=days)
    return ids[:MAX_BULK_USERS]


# --- Инструмент: выдать ешки группе игроков ----------------------------------


async def grant_to_audience(
    session: AsyncSession, *, owner_id: int, user_ids: list[int], amount: int,
    note: str = "",
) -> ToolResult:
    """Начисляет каждому из ``user_ids`` по ``amount`` ешек (с предохранителями)."""
    if amount == 0:
        return ToolResult(ok=False, error="нулевая сумма")
    amount = max(-MAX_GRANT_PER_USER, min(MAX_GRANT_PER_USER, int(amount)))
    targets = list(dict.fromkeys(user_ids))[:MAX_BULK_USERS]
    if not targets:
        return ToolResult(ok=False, error="пустая аудитория")

    done = 0
    skipped_broke = 0
    for uid in targets:
        try:
            if amount < 0:
                # Снятие у группы: с каждого забираем сколько есть (clamp),
                # а не пропускаем тех, у кого меньше суммы. «done» считаем по
                # факту реального списания > 0.
                took, _ = await _debit_clamped_to_balance(
                    session, uid, -amount, REASON_OWNER,
                    {"action": "owner_grant", "note": note, "by": owner_id},
                )
                if took > 0:
                    done += 1
                else:
                    skipped_broke += 1
            else:
                await economy.change_balance(
                    session, uid, amount, REASON_OWNER,
                    {"action": "owner_grant", "note": note, "by": owner_id},
                    allow_negative=False,
                )
                done += 1
        except Exception:  # noqa: BLE001
            logger.debug("grant to %s failed", uid, exc_info=True)
    await _audit(
        session, owner_id, "owner_grant_bulk", None,
        note or "массовая выдача",
        {"amount": amount, "targets": done, "skipped": skipped_broke, "note": note},
    )
    verb = "раздал" if amount > 0 else "снял"
    summary = f"{verb} по {money(abs(amount))} — {done} игрокам"
    if skipped_broke:
        summary += f" (у {skipped_broke} было пусто)"
    return ToolResult(
        ok=done > 0,
        summary=summary,
        affected=done,
        meta={"amount": amount, "per_user": amount, "skipped": skipped_broke},
    )


# --- Инструмент: сбросить кулдаун действия группе ----------------------------


async def reset_cooldown_for(
    session: AsyncSession, *, owner_id: int, user_ids: list[int], action: str,
) -> ToolResult:
    """Сбрасывает кулдаун ``action`` (например ``farm``) у группы игроков."""
    from app.services import cooldowns as cd_service

    action = (action or "farm").strip().lower()
    targets = list(dict.fromkeys(user_ids))[:MAX_BULK_USERS]
    if not targets:
        return ToolResult(ok=False, error="пустая аудитория")
    done = 0
    for uid in targets:
        try:
            await cd_service.clear_cooldown(session, uid, action)
            done += 1
        except Exception:  # noqa: BLE001
            logger.debug("reset cd %s for %s failed", action, uid, exc_info=True)
    await _audit(
        session, owner_id, "owner_reset_cooldown", None,
        f"сброс кд {action}", {"action": action, "targets": done},
    )
    return ToolResult(
        ok=done > 0,
        summary=f"сбросил кулдаун «{action}» — {done} игрокам",
        affected=done,
        meta={"action": action},
    )


# --- Инструмент: розыгрыш ешек среди аудитории -------------------------------


async def giveaway(
    session: AsyncSession, *, owner_id: int, user_ids: list[int], pool: int,
    winners: int = 1, note: str = "",
) -> ToolResult:
    """Разыгрывает ``pool`` ешек между ``winners`` случайными из аудитории."""
    pool = max(1, min(MAX_GIVEAWAY_POOL, int(pool)))
    candidates = list(dict.fromkeys(user_ids))
    if not candidates:
        return ToolResult(ok=False, error="некого разыгрывать")
    winners = max(1, min(int(winners), len(candidates), 20))
    chosen = random.sample(candidates, winners)
    share = pool // winners
    if share <= 0:
        return ToolResult(ok=False, error="фонд меньше числа победителей")

    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, chosen)
    won: list[tuple[int, int]] = []
    win_meta: list[dict] = []
    for uid in chosen:
        try:
            user = await economy.change_balance(
                session, uid, share, REASON_OWNER,
                {"action": "owner_giveaway", "note": note, "by": owner_id},
            )
            won.append((uid, share))
            win_meta.append({
                "id": uid,
                "delta": share,
                "balance": int(getattr(user, "balance", 0) or 0),
            })
        except Exception:  # noqa: BLE001
            logger.debug("giveaway payout to %s failed", uid, exc_info=True)
    await _audit(
        session, owner_id, "owner_giveaway", None, note or "розыгрыш",
        {"pool": pool, "winners": len(won), "share": share},
    )
    winners_str = ", ".join(
        f"{name_for(names, uid)} (+{money(amt)})" for uid, amt in won
    )
    return ToolResult(
        ok=bool(won),
        summary=f"розыгрыш: победител{'и' if len(won) > 1 else 'ь'} — {winners_str}",
        affected=len(won),
        meta={
            "pool": pool,
            "share": share,
            "winners": [u for u, _ in won],
            "targets": win_meta,
        },
    )


# --- Инструмент: выдать/снять одному игроку (по имени/@username) --------------


async def find_user_id(session: AsyncSession, who: str) -> int | None:
    """Ищет игрока по @username, числовому id или отображаемому имени."""
    from app.repositories import users as users_repo

    who = (who or "").strip()
    if not who:
        return None
    if who.startswith("@"):
        u = await users_repo.get_user_by_username(session, who)
        return u.user_id if u else None
    if who.lstrip("-").isdigit():
        return int(who)
    u = await users_repo.get_user_by_username(session, "@" + who)
    if u:
        return u.user_id
    # По отображаемому имени (first_name) — берём самого активного при коллизии.
    row = (
        await session.execute(
            select(User)
            .where(User.first_name.ilike(who))
            .order_by(User.messages_count.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row.user_id if row else None


async def grant_one(
    session: AsyncSession, *, owner_id: int, target_id: int, amount: int, note: str = "",
) -> ToolResult:
    """Начисляет/снимает ешки одному игроку (с предохранителем суммы)."""
    if amount == 0:
        return ToolResult(ok=False, error="нулевая сумма")
    amount = max(-MAX_GRANT_PER_USER, min(MAX_GRANT_PER_USER, int(amount)))
    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    try:
        if amount < 0:
            # Снятие: забираем не больше, чем есть (как admin «забрать»), а
            # не падаем на нехватке. Иначе «забери 5000» у того, у кого 3000,
            # не снимало бы НИЧЕГО — это и был баг снятия денег.
            took, new_balance = await _debit_clamped_to_balance(
                session, target_id, -amount, REASON_OWNER,
                {"action": "owner_grant_one", "note": note, "by": owner_id},
            )
            applied = -took
        else:
            user = await economy.change_balance(
                session, target_id, amount, REASON_OWNER,
                {"action": "owner_grant_one", "note": note, "by": owner_id},
                allow_negative=False,
            )
            applied = amount
            new_balance = int(getattr(user, "balance", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        op = "выдать" if amount > 0 else "снять"
        return ToolResult(ok=False, error=f"не вышло {op} у {nm}: {exc}")
    if applied == 0:
        return ToolResult(ok=False, error=f"у {nm} нечего снимать (баланс пуст)")
    await _audit(
        session, owner_id, "owner_grant_one", target_id, note or "выдача",
        {"amount": applied, "balance_after": new_balance},
    )
    verb = "выдал" if applied > 0 else "снял"
    return ToolResult(
        ok=True,
        summary=f"{verb} {nm} {money(abs(applied))} (стало {money(new_balance)})",
        affected=1,
        meta={
            "amount": applied,
            "target": target_id,
            "targets": [{"id": target_id, "delta": applied, "balance": new_balance}],
        },
    )


# --- Инструмент: мут игрока ---------------------------------------------------


async def mute_one(
    session: AsyncSession, *, owner_id: int, target_id: int, minutes: int,
    reason: str = "",
) -> ToolResult:
    """Мутит игрока на ``minutes`` минут (предохранитель: ≤ 7 дней)."""
    from app.repositories import moderation as mod_repo

    minutes = max(1, min(int(minutes), 7 * 24 * 60))
    until = now_utc() + timedelta(minutes=minutes)
    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    try:
        await mod_repo.set_mute(
            session, target_id, until, reason or "по воле друна", owner_id
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"не вышло замутить {nm}: {exc}")
    await _audit(
        session, owner_id, "owner_mute", target_id, reason or "мут",
        {"minutes": minutes},
    )
    return ToolResult(
        ok=True, summary=f"замутил {nm} на {minutes} мин", affected=1,
        meta={"minutes": minutes, "target": target_id},
    )


# --- Инструмент: множитель ешек (эконом-ивент) -------------------------------


async def set_eshki_multiplier(
    session: AsyncSession, *, owner_id: int, value: float,
) -> ToolResult:
    """Ставит глобальный множитель заработка ешек (предохранитель: 0.1..5x)."""
    from app.models import AppSetting
    from app.settings import dynamic as dyn

    value = max(0.1, min(5.0, float(value)))
    row = await session.get(AppSetting, "modifier.eshki")
    if row is None:
        session.add(
            AppSetting(key="modifier.eshki", value=value, category="economy")
        )
    else:
        row.value = value
    try:
        dyn.invalidate_cache()
    except Exception:  # noqa: BLE001
        pass
    await _audit(
        session, owner_id, "owner_eshki_multiplier", None,
        f"множитель ешек ×{value}", {"value": value},
    )
    return ToolResult(
        ok=True, summary=f"множитель заработка ешек теперь ×{value:g}",
        meta={"value": value},
    )


# --- Инструмент: снять мут с игрока ------------------------------------------


async def unmute_one(
    session: AsyncSession, *, owner_id: int, target_id: int,
) -> ToolResult:
    """Снимает мут с игрока (DB-мут, его читает MuteEnforcementMiddleware)."""
    from app.repositories import moderation as mod_repo
    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    try:
        await mod_repo.set_mute(session, target_id, None, None, owner_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"не вышло размутить {nm}: {exc}")
    await _audit(session, owner_id, "owner_unmute", target_id, "размут", {})
    return ToolResult(
        ok=True, summary=f"снял мут с {nm}", affected=1,
        meta={"target": target_id},
    )


# --- Инструмент: бан/разбан/кик игрока ---------------------------------------


async def _mark_tg_pending(session: AsyncSession, target_id: int) -> None:
    """Помечает строку модерации для применения в Telegram фоновым тиком.

    Бан/кик DB-уровня сам по себе не ограничит в Telegram — это делает
    moderation-scheduler, который читает ``tg_pending`` и зовёт Bot API. Так
    друн получает тот же эффект, что и сайт, не имея bot-объекта под рукой.
    """
    from sqlalchemy import update

    from app.models import UserModeration

    await session.execute(
        update(UserModeration)
        .where(UserModeration.user_id == target_id)
        .values(tg_pending=True)
    )


async def ban_one(
    session: AsyncSession, *, owner_id: int, target_id: int, days: int = 0,
    reason: str = "",
) -> ToolResult:
    """Банит игрока. ``days=0`` — перманентно. Применение в TG — через тик.

    Предохранитель: ≤ 365 дней. Эффект в Telegram накатывает
    moderation-scheduler по флагу ``tg_pending``.
    """
    from app.repositories import moderation as mod_repo
    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    # Нормализуем срок: только явный 0 = перманент. Отрицательные/мусорные
    # значения от планировщика НЕ должны эскалировать до вечного бана.
    days = max(0, min(int(days), 365))
    until = None
    if days > 0:
        until = now_utc() + timedelta(days=days)
    try:
        await mod_repo.set_ban(
            session, target_id, until, reason or "по воле друна", owner_id
        )
        await _mark_tg_pending(session, target_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"не вышло забанить {nm}: {exc}")
    span = "навсегда" if until is None else f"на {days} дн"
    await _audit(
        session, owner_id, "owner_ban", target_id, reason or "бан",
        {"days": days},
    )
    return ToolResult(
        ok=True, summary=f"забанил {nm} {span}", affected=1,
        meta={"target": target_id, "days": days},
    )


async def unban_one(
    session: AsyncSession, *, owner_id: int, target_id: int,
) -> ToolResult:
    """Снимает бан с игрока (DB + применение в TG через тик)."""
    from app.repositories import moderation as mod_repo
    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    try:
        await mod_repo.set_ban(session, target_id, None, None, owner_id)
        await _mark_tg_pending(session, target_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"не вышло разбанить {nm}: {exc}")
    await _audit(session, owner_id, "owner_unban", target_id, "разбан", {})
    return ToolResult(
        ok=True, summary=f"снял бан с {nm}", affected=1,
        meta={"target": target_id},
    )


async def kick_one(
    session: AsyncSession, *, owner_id: int, target_id: int, reason: str = "",
) -> ToolResult:
    """Кикает игрока: короткий бан, который затем снимает scheduler.

    Telegram-кик = ban + последующий unban. Ставим бан на 5 минут с
    ``tg_pending``: moderation-scheduler (тик раз в минуту) гарантированно
    увидит активный бан и удалит игрока из чата, а ``due_unbans`` снимет бан
    после истечения — игрок сможет вернуться по ссылке. ВАЖНО: срок должен быть
    заметно больше интервала тика (1 мин), иначе бан истечёт раньше, чем тик
    его применит, и кик не сработает.
    """
    from app.repositories import moderation as mod_repo
    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    until = now_utc() + timedelta(minutes=5)
    try:
        await mod_repo.set_ban(
            session, target_id, until, reason or "кик друна", owner_id
        )
        await _mark_tg_pending(session, target_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"не вышло кикнуть {nm}: {exc}")
    await _audit(session, owner_id, "owner_kick", target_id, reason or "кик", {})
    return ToolResult(
        ok=True, summary=f"кикнул {nm}", affected=1, meta={"target": target_id},
    )


# --- Инструмент: варн игрока (с авто-мутом по порогу) ------------------------


async def warn_one(
    session: AsyncSession, *, owner_id: int, target_id: int, reason: str = "",
) -> ToolResult:
    """Выдаёт варн; при достижении порога — авто-мут (DB-мут, как у модерации).

    TG-side restriction (apply_mute_telegram) тут НЕ зовём — нет bot-объекта;
    но DB-мут читает MuteEnforcementMiddleware и удаляет сообщения, так что
    эффект для чата реальный.
    """
    from app.repositories import moderation as mod_repo
    from app.settings import moderation as mod_settings
    from app.core.utils import now_utc as _now
    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    ttl = _now() + timedelta(seconds=mod_settings.WARN_TTL_SECONDS)
    try:
        count = await mod_repo.add_warning(
            session, target_id, owner_id, reason or "по воле друна", ttl
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"не вышло варнуть {nm}: {exc}")
    automuted = False
    if count >= mod_settings.WARN_MUTE_THRESHOLD:
        until = _now() + timedelta(seconds=mod_settings.WARN_MUTE_SECONDS)
        try:
            # ВНИМАНИЕ: команда /warn модерации на пороге зовёт ОБА —
            # apply_mute_telegram (реальный TG-restrict) И set_mute (DB). Здесь
            # bot-объекта нет, поэтому ставим ТОЛЬКО DB-мут. Он не банит ввод в
            # Telegram, но MuteEnforcementMiddleware удаляет сообщения мутнутого
            # — эффект для чата есть, хоть и не полный паритет с /warn.
            await mod_repo.set_mute(
                session, target_id, until, "авто-мьют по варнам", owner_id
            )
            automuted = True
        except Exception:  # noqa: BLE001
            logger.debug("warn automute failed for %s", target_id, exc_info=True)
    await _audit(
        session, owner_id, "owner_warn", target_id, reason or "варн",
        {"count": count, "automuted": automuted},
    )
    summary = f"варнул {nm} ({count}/{mod_settings.WARN_MUTE_THRESHOLD})"
    if automuted:
        summary += " — порог, авто-мут"
    return ToolResult(
        ok=True, summary=summary, affected=1,
        meta={"target": target_id, "count": count, "automuted": automuted},
    )


# --- Инструмент: снять все варны ---------------------------------------------


async def unwarn_one(
    session: AsyncSession, *, owner_id: int, target_id: int,
) -> ToolResult:
    """Снимает все активные варны игрока."""
    from app.repositories import moderation as mod_repo
    from app.features.drun.names import name_for, resolve_names

    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    try:
        cleared = await mod_repo.clear_warnings(session, target_id, owner_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"не вышло снять варны у {nm}: {exc}")
    await _audit(
        session, owner_id, "owner_unwarn", target_id, "снятие варнов",
        {"cleared": cleared},
    )
    if cleared <= 0:
        return ToolResult(ok=True, summary=f"у {nm} не было активных варнов",
                          affected=0, meta={"target": target_id})
    return ToolResult(
        ok=True, summary=f"снял {cleared} варн(ов) с {nm}", affected=1,
        meta={"target": target_id, "cleared": cleared},
    )


# --- Инструмент: начислить/снять MMR -----------------------------------------

MAX_MMR_DELTA = 1000  # потолок изменения рейтинга за одну операцию


async def award_mmr_one(
    session: AsyncSession, *, owner_id: int, target_id: int, amount: int,
) -> ToolResult:
    """Начисляет (или снимает при amount<0) MMR игроку. Кламп: ±1000.

    ВАЖНО: кламп применяется ДО множителей опыта. award_mmr для начислений
    (>0) домножает на modifier.xp / season.xp_bonus из админки, поэтому при
    активном ивенте фактический прирост может превысить 1000 (напр. ×2 → 2000).
    Это осознанно: потолок страхует от опечатки owner'а, а ивент-бонус —
    отдельная управляемая операторами величина. Списания (<0) не масштабируются.
    """
    from app.features.mmr import service as mmr_service
    from app.features.drun.names import name_for, resolve_names

    if amount == 0:
        return ToolResult(ok=False, error="нулевой MMR")
    amount = max(-MAX_MMR_DELTA, min(MAX_MMR_DELTA, int(amount)))
    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    try:
        await mmr_service.award_mmr(
            session, player_id=target_id, amount=amount,
            source="owner_drun", reason="по воле друна",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"не вышло изменить MMR у {nm}: {exc}")
    await _audit(
        session, owner_id, "owner_award_mmr", target_id, "MMR", {"amount": amount},
    )
    verb = "накинул" if amount > 0 else "срезал"
    return ToolResult(
        ok=True, summary=f"{verb} {nm} {abs(amount)} MMR", affected=1,
        meta={"target": target_id, "amount": amount},
    )


# --- Инструмент: выдать предмет в инвентарь ----------------------------------

MAX_ITEM_QTY = 100  # потолок штук за одну выдачу


async def grant_item_one(
    session: AsyncSession, *, owner_id: int, target_id: int, item_code: str,
    quantity: int = 1,
) -> ToolResult:
    """Выдаёт предмет ``item_code`` игроку (кламп кол-ва 1..100)."""
    from app.services import inventory_grant
    from app.features.drun.names import name_for, resolve_names

    item_code = (item_code or "").strip()
    if not item_code:
        return ToolResult(ok=False, error="не указан предмет")
    quantity = max(1, min(int(quantity), MAX_ITEM_QTY))
    names = await resolve_names(session, [target_id])
    nm = name_for(names, target_id)
    try:
        await inventory_grant.grant_item(
            session, user_id=target_id, item_code=item_code, quantity=quantity,
            source="owner_drun", event="owner_grant", actor_user_id=owner_id,
        )
    except inventory_grant.UnknownItem:
        return ToolResult(ok=False, error=f"нет такого предмета: «{item_code}»")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"не вышло выдать предмет {nm}: {exc}")
    await _audit(
        session, owner_id, "owner_grant_item", target_id, "выдача предмета",
        {"item": item_code, "qty": quantity},
    )
    return ToolResult(
        ok=True, summary=f"выдал {nm} «{item_code}» ×{quantity}", affected=1,
        meta={"target": target_id, "item": item_code, "qty": quantity},
    )
