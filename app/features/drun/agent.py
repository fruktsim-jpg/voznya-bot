"""Агент-планировщик владельца: естественный язык → вызов инструмента.

Владелец пишет в чате «друн дай всем кто писал за час по 100 ешек» / «друн
сбрось всем кд на ферму» / «друн разыграй 5000 среди активных». Этот модуль:

1. проверяет, что автор — owner (``ADMIN_IDS``); иначе агент недоступен;
2. просит LLM разобрать намерение в СТРОГИЙ JSON {tool, args};
3. валидирует и исполняет инструмент из ``tools.py`` с предохранителями;
4. возвращает результат, чтобы друн отчитался в чате в своём стиле.

Это НЕ обычный ответ-болтовня: если намерение не распознано как команда —
возвращаем ``handled=False`` и обычный диалоговый путь обрабатывает реплику.

Безопасность: только owner; набор инструментов закрытый (white-list); суммы и
аудитории клампятся в самих инструментах; всё пишется в audit_log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logger import get_logger
from app.features.drun import config as drun_config
from app.features.drun import provider as drun_provider
from app.features.drun import tools as drun_tools

logger = get_logger(__name__)


@dataclass
class AgentOutcome:
    """Итог попытки выполнить owner-команду."""

    handled: bool                  # была ли это вообще команда-намерение
    ok: bool = False               # успешно ли выполнена
    summary: str = ""              # человекочитаемый итог для друна
    tool: str = ""
    meta: dict = field(default_factory=dict)  # структурные данные (targets для тегов)


_SYSTEM = (
    "Ты — диспетчер команд владельца игрового чата «Возня». Владелец пишет "
    "обычным человеческим языком (часто коряво, с опечатками, сленгом), что "
    "сделать с игроками/экономикой. Твоя задача — ПОНЯТЬ намерение (даже если "
    "сформулировано криво) и вернуть СТРОГО JSON для исполнения. Никакого текста "
    "вокруг.\n"
    "\n"
    "Доступные инструменты (tool):\n"
    "- grant: выдать (или снять, если сумма отрицательная) ешки аудитории.\n"
    "    args: amount (int), scope ('recent'|'active'|'all'),\n"
    "          minutes (int, для recent), days (int, для active), note (str).\n"
    "- grant_one: выдать/снять ешки ОДНОМУ игроку.\n"
    "    args: who (str: @username/имя/id), amount (int), note (str).\n"
    "- reset_cooldown: сбросить кулдаун действия аудитории.\n"
    "    args: action ('farm'|'casino'|...), scope, minutes, days.\n"
    "- giveaway: розыгрыш призового фонда между случайными из аудитории.\n"
    "    args: pool (int, всего ешек), winners (int), scope, minutes, days, note.\n"
    "- mute: замутить одного игрока на время.\n"
    "    args: who (str), minutes (int), reason (str).\n"
    "- multiplier: глобальный множитель заработка ешек (эконом-ивент).\n"
    "    args: value (float, напр. 2 = x2).\n"
    "- spawn_treasure: вкинуть клад (раздачу) в чат прямо сейчас.\n"
    "    args: {} (без аргументов).\n"
    "- none: это НЕ команда-действие, а обычная болтовня/вопрос.\n"
    "\n"
    "Правила разбора (будь гибким, читай смысл, а не точные слова):\n"
    "- «кто писал за час/последний час/недавно» → scope=recent, minutes=60.\n"
    "- «активным/за неделю/кто играет» → scope=active, days=7.\n"
    "- «всем/каждому/всему чату» без уточнения → scope=active, days=7 "
    "(безопаснее, чем all).\n"
    "- «дай/выдай/закинь/накинь/подари по 100» → grant amount=100.\n"
    "- «забери/сними/отними/штрафани 50» → grant/grant_one amount=-50.\n"
    "- «забери всё/обнули баланс/обнули» у игрока → grant_one с большим "
    "отрицательным amount (напр. -100000000): снимется ровно сколько есть, "
    "в минус не уйдёт.\n"
    "- Конкретный человек («@vasya», «дай Пете», «сними у Кота») → grant_one с "
    "who. «разыграй»/«розыгрыш»/«раздача с победителями» → giveaway.\n"
    "- «замути/мут/заткни» → mute. «x2/удвой/множитель/ивент на ешки» → "
    "multiplier. «клад/сокровище/спавн раздачи» → spawn_treasure.\n"
    "- Сумму понимай в любом виде: «1к»=1000, «5к»=5000, «лям»=1000000.\n"
    "- Если намерение действия ЕСТЬ, но непонятны детали — выбери разумные "
    "дефолты, не отказывайся. Отказ (tool=none) — только если это реально "
    "вопрос/шутка/болтовня, а не команда.\n"
    "Верни строго: {\"tool\":\"...\",\"args\":{...}}"
)


def is_owner(user_id: int) -> bool:
    """Owner-гейт: только пользователи из ADMIN_IDS управляют миром."""
    return get_settings().is_admin(user_id)


async def _plan(session: AsyncSession, text: str) -> dict | None:
    """Просит LLM разобрать намерение владельца в JSON {tool, args}."""
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return None
    user_msg = f"Команда владельца: «{text}»\n\nВерни JSON."
    try:
        raw = await drun_provider.chat(
            cfg, system=_SYSTEM, messages=[{"role": "user", "content": user_msg}],
            model=cfg.model_for(drun_config.ROLE_PLANNING),
        )
    except drun_provider.LlmError as exc:
        logger.debug("agent plan llm failed: %s", exc)
        return None
    s = (raw or "").strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(s[start : end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _arg_int(args: dict, key: str, default: int = 0) -> int:
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        return default


async def try_handle(
    session: AsyncSession, *, owner_id: int, text: str
) -> AgentOutcome:
    """Пытается распознать и выполнить owner-команду из реплики.

    Возвращает ``handled=False``, если это не команда (обычная болтовня) —
    тогда вызывающий идёт обычным диалоговым путём.
    """
    if not is_owner(owner_id):
        return AgentOutcome(handled=False)

    plan = await _plan(session, text)
    if not plan:
        return AgentOutcome(handled=False)
    tool = str(plan.get("tool", "none")).strip().lower()
    args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
    if tool in ("none", ""):
        return AgentOutcome(handled=False)

    scope = str(args.get("scope", "active")).strip().lower()
    minutes = _arg_int(args, "minutes", 60)
    days = _arg_int(args, "days", 7)
    note = str(args.get("note", "")).strip()[:120]

    # Инструменты без аудитории (точечные / глобальные).
    if tool == "grant_one":
        who = str(args.get("who", "")).strip()
        target = await drun_tools.find_user_id(session, who)
        if target is None:
            return AgentOutcome(
                handled=True, ok=False,
                summary=f"не нашёл игрока «{who}»", tool=tool,
            )
        res = await drun_tools.grant_one(
            session, owner_id=owner_id, target_id=target,
            amount=_arg_int(args, "amount", 0), note=note,
        )
        return AgentOutcome(
            handled=True, ok=res.ok, summary=res.summary or res.error,
            tool=tool, meta=res.meta,
        )

    if tool == "mute":
        who = str(args.get("who", "")).strip()
        target = await drun_tools.find_user_id(session, who)
        if target is None:
            return AgentOutcome(
                handled=True, ok=False, summary=f"не нашёл игрока «{who}»", tool=tool
            )
        res = await drun_tools.mute_one(
            session, owner_id=owner_id, target_id=target,
            minutes=_arg_int(args, "minutes", 30),
            reason=str(args.get("reason", "")).strip()[:120],
        )
        return AgentOutcome(handled=True, ok=res.ok, summary=res.summary or res.error, tool=tool)

    if tool == "multiplier":
        try:
            value = float(args.get("value", 1))
        except (TypeError, ValueError):
            value = 1.0
        res = await drun_tools.set_eshki_multiplier(
            session, owner_id=owner_id, value=value
        )
        return AgentOutcome(handled=True, ok=res.ok, summary=res.summary or res.error, tool=tool)

    if tool == "spawn_treasure":
        # Спавн клада требует bot+sessionmaker и своей сессии — помечаем как
        # отложенное действие, исполнит вызывающий (reply_handlers).
        return AgentOutcome(
            handled=True, ok=True, summary="__spawn_treasure__", tool=tool
        )

    audience = await drun_tools.resolve_audience(
        session, scope=scope, minutes=minutes, days=days
    )

    if tool == "grant":
        amount = _arg_int(args, "amount", 0)
        res = await drun_tools.grant_to_audience(
            session, owner_id=owner_id, user_ids=audience, amount=amount, note=note
        )
    elif tool == "reset_cooldown":
        action = str(args.get("action", "farm")).strip().lower()
        res = await drun_tools.reset_cooldown_for(
            session, owner_id=owner_id, user_ids=audience, action=action
        )
    elif tool == "giveaway":
        pool = _arg_int(args, "pool", 0)
        winners = _arg_int(args, "winners", 1)
        res = await drun_tools.giveaway(
            session, owner_id=owner_id, user_ids=audience, pool=pool,
            winners=winners, note=note,
        )
    else:
        return AgentOutcome(handled=False)

    return AgentOutcome(
        handled=True, ok=res.ok, summary=res.summary or res.error,
        tool=tool, meta=res.meta,
    )


# Дешёвый пред-фильтр: гоняем LLM-планировщик только если в реплике владельца
# есть глагол-намерение действия. Иначе обычная болтовня owner'а («друн как
# дела») зря дёргала бы модель и могла бы быть истолкована как команда.
_ACTION_HINTS = (
    "дай", "выдай", "раздай", "начисли", "закинь", "накинь", "подкинь",
    "подари", "награди", "плюсани", "докинь", "кинь",
    "забери", "сними", "отними", "штраф", "оштрафуй", "минусани", "обнули баланс",
    "сбрось", "сбрось кд", "обнули", "ресетни", "reset", "откати кд", "скинь кд",
    "разыграй", "розыгрыш", "раздача", "giveaway", "разыгран", "разыгровка",
    "всем", "каждому", "активным", "кто писал", "участник", "победител",
    "замуть", "замути", "мут", "заткни", "забань", "бан",
    "множитель", "x2", "х2", "удвой", "ивент", "буст",
    "клад", "клады", "сокровище", "спавн", "раздай клад",
)


def looks_like_action(text: str) -> bool:
    """Грубая проверка: похоже ли на команду-действие (чтобы не звать LLM зря)."""
    low = text.lower()
    return any(h in low for h in _ACTION_HINTS)
