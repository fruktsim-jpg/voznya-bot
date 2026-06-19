"""Команды автономных ивентов друна (Phase 4).

Игроки:
* ``/ивенты`` — список активных ивентов друна (челленджи/прогнозы/мини-ивенты);
* ``/участвую [N] [вариант]`` — записаться в ивент (по номеру или в единственный
  активный); ``вариант`` — для прогнозов (на что ставишь).

Владелец:
* ``/ивент <тип> | <заголовок> | <награда> | <часы>`` — запустить ивент вручную.
  Тип: челлендж|прогноз|мини|цель. Друн умеет и сам инициировать движ (через
  autonomous), это ручной триггер.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.features.drun import events as drun_events

router = Router(name="drun_events")

_KIND_ALIASES = {
    "челлендж": drun_events.KIND_CHALLENGE,
    "challenge": drun_events.KIND_CHALLENGE,
    "прогноз": drun_events.KIND_PREDICTION,
    "prediction": drun_events.KIND_PREDICTION,
    "мини": drun_events.KIND_MINI_EVENT,
    "мини-ивент": drun_events.KIND_MINI_EVENT,
    "цель": drun_events.KIND_GOAL,
    "goal": drun_events.KIND_GOAL,
}

_KIND_RU = {
    drun_events.KIND_CHALLENGE: "челлендж",
    drun_events.KIND_PREDICTION: "прогноз",
    drun_events.KIND_MINI_EVENT: "мини-ивент",
    drun_events.KIND_GOAL: "цель",
}


def _is_admin(message: Message) -> bool:
    return (
        message.from_user is not None
        and get_settings().is_admin(message.from_user.id)
    )


@router.message(RuCommand("ивенты", "events", allow_no_prefix=False))
async def cmd_events(message: Message, session: AsyncSession, command_args: str) -> None:
    """Список активных ивентов друна."""
    items = await drun_events.list_active(session)
    if not items:
        await message.answer("Сейчас активных ивентов нет. Скоро будут.")
        return
    lines = ["🎯 Активные ивенты друна:"]
    for ev in items:
        kind_ru = _KIND_RU.get(ev.kind, ev.kind)
        reward = f" • награда {ev.reward_amount} ешек" if ev.reward_amount else ""
        joined = len(ev.participants or [])
        lines.append(f"#{ev.id} [{kind_ru}] {ev.title}{reward} • участников: {joined}")
    lines.append("\nЗаписаться: /участвую N (для прогноза — /участвую N вариант)")
    await message.answer("\n".join(lines))


@router.message(RuCommand("участвую", "join", allow_no_prefix=False))
async def cmd_join(message: Message, session: AsyncSession, command_args: str) -> None:
    """Записывает игрока в ивент (по номеру или в единственный активный)."""
    user = message.from_user
    if user is None:
        return

    parts = command_args.strip().split(maxsplit=1)
    event_id: int | None = None
    choice: str | None = None
    if parts and parts[0].isdigit():
        event_id = int(parts[0])
        choice = parts[1].strip() if len(parts) > 1 else None
    else:
        # Номер не указан — берём единственный активный ивент.
        active = await drun_events.list_active(session, limit=2)
        if len(active) == 1:
            event_id = active[0].id
            choice = command_args.strip() or None
        elif len(active) > 1:
            await message.answer("Активных ивентов несколько — укажи номер: /участвую N")
            return
        else:
            await message.answer("Активных ивентов нет.")
            return

    res = await drun_events.join_event(
        session, event_id=event_id, user_id=user.id, choice=choice
    )
    if res.ok:
        await message.answer(f"Ты в игре (ивент #{event_id}). Дерзай.")
        return
    msg = {
        "not_found": "Нет такого ивента.",
        "not_active": "Этот ивент уже закрыт.",
        "expired": "Дедлайн ивента вышел.",
        "already_joined": "Ты уже записан в этот ивент.",
    }.get(res.error, "Не получилось записаться.")
    await message.answer(msg)


@router.message(RuCommand("ивент", "event", allow_no_prefix=False))
async def cmd_create_event(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """/ивент <тип> | <заголовок> | <награда> | <часы> — запуск ивента владельцем."""
    if not _is_admin(message):
        return
    raw = command_args.strip()
    if not raw:
        await message.answer(
            "Формат: /ивент <тип> | <заголовок> | <награда> | <часы>\n"
            "Типы: челлендж, прогноз, мини, цель. Награда/часы — необязательны.\n"
            "Пример: /ивент челлендж | Первый, кто наберёт 5 побед в дуэлях | 1000 | 6"
        )
        return

    segs = [s.strip() for s in raw.split("|")]
    kind = _KIND_ALIASES.get(segs[0].lower(), drun_events.KIND_MINI_EVENT)
    title = segs[1] if len(segs) > 1 and segs[1] else segs[0]
    reward = 0
    ttl = drun_events._DEFAULT_TTL_HOURS
    if len(segs) > 2 and segs[2].isdigit():
        reward = int(segs[2])
    if len(segs) > 3 and segs[3].isdigit():
        ttl = int(segs[3])

    res = await drun_events.create_event(
        session,
        kind=kind,
        title=title,
        created_by=message.from_user.id,
        reward_amount=reward,
        ttl_hours=ttl,
    )
    if not res.ok:
        msg = {
            "too_many": "Уже идёт максимум ивентов — заверши текущие сначала.",
            "empty_title": "Нужен заголовок ивента.",
        }.get(res.error, "Не вышло создать ивент.")
        await message.answer(msg)
        return

    kind_ru = _KIND_RU.get(kind, kind)
    reward_str = f" Награда: {reward} ешек." if reward else ""
    await message.answer(
        f"🎯 Запущен {kind_ru} #{res.event_id}: {title}.{reward_str}\n"
        f"Записаться: /участвую {res.event_id}"
    )


@router.message(RuCommand("ивентотмена", "eventcancel", allow_no_prefix=False))
async def cmd_cancel_event(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """/ивентотмена N — отменить ивент без выплат (только владелец)."""
    if not _is_admin(message):
        return
    arg = command_args.strip()
    if not arg.isdigit():
        await message.answer("Формат: /ивентотмена N (номер ивента).")
        return
    res = await drun_events.cancel_event(session, event_id=int(arg))
    if res.ok:
        await message.answer(f"Ивент #{arg} отменён.")
        return
    msg = {
        "not_found": "Нет такого ивента.",
        "not_cancellable": "Этот ивент уже завершён или отменён.",
    }.get(res.error, "Не вышло отменить ивент.")
    await message.answer(msg)
