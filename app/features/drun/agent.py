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
from app.features.drun import registry as drun_registry
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


def _build_system() -> str:
    """Системный промпт диспетчера. Каталог инструментов берётся из реестра
    (registry.REGISTRY) — единый источник правды, не дублируем список руками.
    """
    return (
        "Ты — диспетчер команд владельца игрового чата «Возня». Владелец пишет "
        "обычным человеческим языком (часто коряво, с опечатками, сленгом), что "
        "сделать с игроками/экономикой. Твоя задача — ПОНЯТЬ намерение (даже если "
        "сформулировано криво) и вернуть СТРОГО JSON для исполнения. Никакого "
        "текста вокруг.\n"
        "\n"
        "Доступные инструменты (tool):\n"
        f"{drun_registry.build_catalog()}\n"
        "- none: это НЕ команда-действие, а обычная болтовня/вопрос.\n"
        "\n"
        "Поля who — @username/имя/id игрока. scope: 'recent' (писали за minutes "
        "мин), 'active' (играли за days дней), 'all' (все), 'poorest' (N самых "
        "нищих), 'richest' (N самых богатых).\n"
        "\n"
        "Правила разбора (будь гибким, читай смысл, а не точные слова):\n"
        "- «кто писал за час/последний час/недавно» → scope=recent, minutes=60.\n"
        "- «активным/за неделю/кто играет» → scope=active, days=7.\n"
        "- «всем/каждому/всему чату» без уточнения → scope=active, days=7 "
        "(безопаснее, чем all).\n"
        "- «N самым бедным/нищим/у кого мало» → scope=poorest, limit=N. «N самым "
        "богатым/топам/у кого больше всех» → scope=richest, limit=N. ВСЕГДА "
        "указывай limit, если назвали число получателей («5 нищим» → limit=5). "
        "Это критично: без limit раздаст всем подряд, а не пятерым.\n"
        "- «дай/выдай/закинь/накинь/подари по 100» → grant amount=100.\n"
        "- «забери/сними/отними/штрафани 50» → grant/grant_one amount=-50.\n"
        "- «забери всё/обнули баланс/обнули» у игрока → grant_one с большим "
        "отрицательным amount (напр. -100000000): снимется ровно сколько есть, "
        "в минус не уйдёт.\n"
        "- Конкретный человек («@vasya», «дай Пете», «сними у Кота») → grant_one "
        "с who. «разыграй»/«розыгрыш»/«раздача с победителями» → giveaway.\n"
        "- «замути/мут/заткни» → mute, «размуть/сними мут» → unmute.\n"
        "- «варн/предупреди» → warn, «сними варн/прости» → unwarn.\n"
        "- «накинь/срежь ММР/рейтинг» → award_mmr (amount±). «выдай предмет/шмот» "
        "→ grant_item (item=код предмета, quantity).\n"
        "- «x2/удвой/множитель/ивент на ешки» → multiplier. «клад/сокровище/спавн "
        "раздачи» → spawn_treasure.\n"
        "- Сумму понимай в любом виде: «1к»=1000, «5к»=5000, «лям»=1000000.\n"
        "- Если намерение действия ЕСТЬ, но непонятны детали — выбери разумные "
        "дефолты, не отказывайся. Отказ (tool=none) — только если это реально "
        "вопрос/шутка/болтовня, а не команда.\n"
        "Верни строго: {\"tool\":\"...\",\"args\":{...}}"
    )


_SYSTEM = _build_system()


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


async def try_handle(
    session: AsyncSession, *, owner_id: int, text: str
) -> AgentOutcome:
    """Пытается распознать и выполнить owner-команду из реплики.

    Возвращает ``handled=False``, если это не команда (обычная болтовня) —
    тогда вызывающий идёт обычным диалоговым путём. Диспетчеризация — через
    единый реестр (registry.REGISTRY): тут нет per-tool лесенки if/elif.
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

    # Резолверы для хендлеров реестра (он не знает деталей tools.py).
    async def _resolve_who(who: str) -> int | None:
        return await drun_tools.find_user_id(session, who)

    async def _resolve_audience(
        *, scope: str, minutes: int, days: int, limit: int | None = None
    ) -> list[int]:
        return await drun_tools.resolve_audience(
            session, scope=scope, minutes=minutes, days=days, limit=limit
        )

    ctx = drun_registry.ToolContext(
        session=session, owner_id=owner_id, args=args,
        resolve_who=_resolve_who, resolve_audience=_resolve_audience,
    )
    res = await drun_registry.dispatch(ctx, tool)
    if res is None:
        # Планировщик выдумал несуществующий тул — трактуем как болтовню.
        logger.debug("agent: unknown tool %r — falling back to chat", tool)
        return AgentOutcome(handled=False)

    return AgentOutcome(
        handled=True, ok=res.ok, summary=res.summary or res.error,
        tool=tool, meta=res.meta,
    )


# Дешёвый пред-фильтр: гоняем LLM-планировщик только если в реплике владельца
# есть глагол-намерение действия. Иначе обычная болтовня owner'а («друн как
# дела») зря дёргала бы модель и могла бы быть истолкована как команда.
# Базовые подсказки + подсказки из реестра (каждый тул несёт свои глаголы) —
# так добавление тула автоматически расширяет пред-фильтр, без правки тут.
_BASE_HINTS = (
    "плюсани", "обнули", "ресетни", "reset",
    "всем", "каждому", "активным", "кто писал", "участник", "победител",
)
_ACTION_HINTS = tuple(dict.fromkeys(_BASE_HINTS + drun_registry.all_hints()))


def looks_like_action(text: str) -> bool:
    """Грубая проверка: похоже ли на команду-действие (чтобы не звать LLM зря)."""
    low = text.lower()
    return any(h in low for h in _ACTION_HINTS)
