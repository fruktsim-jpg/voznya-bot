"""Единый реестр действий друна (Phase 2).

Раньше agent.py хардкодил разбор каждого инструмента в длинной лесенке if/elif,
а список тулов был продублирован в тексте системного промпта. Это плодило
рассинхрон: добавил тул в tools.py — забыл в промпте или в диспетчере.

Здесь — единый декларативный источник правды. Каждый ToolSpec описывает:
* name        — имя тула (то, что вернёт планировщик в JSON);
* summary     — однострочное описание для каталога в промпте;
* args_doc    — описание аргументов для промпта;
* kind        — как добывать цель(и): "audience" / "who" / "none";
* run         — корутина-исполнитель (общая сигнатура), зовущая tools.py.

agent.py берёт отсюда и каталог для промпта (build_catalog), и диспетчер
(dispatch). Добавление нового действия = одна запись в REGISTRY.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.features.drun import tools as drun_tools

logger = get_logger(__name__)


# Контекст одного исполнения: всё, что может понадобиться хендлеру.
@dataclass
class ToolContext:
    session: AsyncSession
    owner_id: int
    args: dict
    # Резолверы передаются из agent.py, чтобы реестр не зависел от деталей.
    resolve_who: Callable[[str], Awaitable[int | None]]
    resolve_audience: Callable[..., Awaitable[list[int]]]  # принимает scope/minutes/days/limit

    def arg_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.args.get(key, default))
        except (TypeError, ValueError):
            return default

    def arg_str(self, key: str, default: str = "", *, limit: int = 120) -> str:
        return str(self.args.get(key, default)).strip()[:limit]

    def arg_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.args.get(key, default))
        except (TypeError, ValueError):
            return default


# Хендлер возвращает ToolResult из tools.py.
ToolHandler = Callable[[ToolContext], Awaitable[drun_tools.ToolResult]]


@dataclass
class ToolSpec:
    name: str
    summary: str               # для каталога в промпте
    args_doc: str              # для каталога в промпте
    run: ToolHandler
    hints: tuple[str, ...] = field(default_factory=tuple)  # пред-фильтр глаголов


# --- Хендлеры: тонкие адаптеры args → вызов tools.py -------------------------


async def _h_grant(ctx: ToolContext) -> drun_tools.ToolResult:
    audience = await ctx.resolve_audience(
        scope=ctx.arg_str("scope", "active").lower(),
        minutes=ctx.arg_int("minutes", 60), days=ctx.arg_int("days", 7),
        limit=ctx.arg_int("limit", 0) or None,
    )
    return await drun_tools.grant_to_audience(
        ctx.session, owner_id=ctx.owner_id, user_ids=audience,
        amount=ctx.arg_int("amount", 0), note=ctx.arg_str("note"),
    )


async def _h_grant_one(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.grant_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target,
        amount=ctx.arg_int("amount", 0), note=ctx.arg_str("note"),
    )


async def _h_reset_cooldown(ctx: ToolContext) -> drun_tools.ToolResult:
    audience = await ctx.resolve_audience(
        scope=ctx.arg_str("scope", "active").lower(),
        minutes=ctx.arg_int("minutes", 60), days=ctx.arg_int("days", 7),
    )
    return await drun_tools.reset_cooldown_for(
        ctx.session, owner_id=ctx.owner_id, user_ids=audience,
        action=ctx.arg_str("action", "farm").lower(),
    )


async def _h_giveaway(ctx: ToolContext) -> drun_tools.ToolResult:
    audience = await ctx.resolve_audience(
        scope=ctx.arg_str("scope", "active").lower(),
        minutes=ctx.arg_int("minutes", 60), days=ctx.arg_int("days", 7),
        limit=ctx.arg_int("limit", 0) or None,
    )
    return await drun_tools.giveaway(
        ctx.session, owner_id=ctx.owner_id, user_ids=audience,
        pool=ctx.arg_int("pool", 0), winners=ctx.arg_int("winners", 1),
        note=ctx.arg_str("note"),
    )


async def _h_mute(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.mute_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target,
        minutes=ctx.arg_int("minutes", 30), reason=ctx.arg_str("reason"),
    )


async def _h_unmute(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.unmute_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target
    )


async def _h_warn(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.warn_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target,
        reason=ctx.arg_str("reason"),
    )


async def _h_unwarn(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.unwarn_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target
    )


async def _h_award_mmr(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.award_mmr_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target,
        amount=ctx.arg_int("amount", 0),
    )


async def _h_ban(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.ban_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target,
        days=ctx.arg_int("days", 0), reason=ctx.arg_str("reason"),
    )


async def _h_unban(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.unban_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target
    )


async def _h_kick(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.kick_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target,
        reason=ctx.arg_str("reason"),
    )


async def _h_websearch(ctx: ToolContext) -> drun_tools.ToolResult:
    from app.features.drun import websearch as drun_web

    res = await drun_web.search(ctx.session, ctx.arg_str("query", limit=300))
    if not res.ok:
        return drun_tools.ToolResult(
            ok=False, error=f"веб недоступен ({res.error})"
        )
    return drun_tools.ToolResult(
        ok=True, summary=res.summary, meta={"web": True, "query": res.query},
    )


async def _h_grant_item(ctx: ToolContext) -> drun_tools.ToolResult:
    who = ctx.arg_str("who")
    target = await ctx.resolve_who(who)
    if target is None:
        return drun_tools.ToolResult(ok=False, error=f"не нашёл игрока «{who}»")
    return await drun_tools.grant_item_one(
        ctx.session, owner_id=ctx.owner_id, target_id=target,
        item_code=ctx.arg_str("item"), quantity=ctx.arg_int("quantity", 1),
    )


async def _h_multiplier(ctx: ToolContext) -> drun_tools.ToolResult:
    return await drun_tools.set_eshki_multiplier(
        ctx.session, owner_id=ctx.owner_id, value=ctx.arg_float("value", 1.0)
    )


# Карта «человеческая фича → ключ app_settings» для feature_toggle.
_FEATURE_KEYS = {
    "casino": "casino.enabled", "казино": "casino.enabled",
    "duel": "duel.enabled", "бои": "duel.enabled", "дуэли": "duel.enabled",
    "farm": "farm.enabled", "ферма": "farm.enabled",
    "cases": "cases.enabled", "кейсы": "cases.enabled",
    "shop": "shop.enabled", "магазин": "shop.enabled", "шоп": "shop.enabled",
    "gifts": "gifts.enabled", "подарки": "gifts.enabled",
}


async def _h_feature_toggle(ctx: ToolContext) -> drun_tools.ToolResult:
    feature = ctx.arg_str("feature").lower()
    key = _FEATURE_KEYS.get(feature)
    if key is None:
        return drun_tools.ToolResult(
            ok=False, error=f"не знаю фичу «{feature}»"
        )
    # enable: принимаем bool/строку/число; дефолт — включить.
    raw = ctx.args.get("enable", True)
    return await drun_tools.set_app_setting(
        ctx.session, owner_id=ctx.owner_id, key=key, value=raw,
    )


async def _h_set_param(ctx: ToolContext) -> drun_tools.ToolResult:
    return await drun_tools.set_app_setting(
        ctx.session, owner_id=ctx.owner_id,
        key=ctx.arg_str("key"), value=ctx.args.get("value"),
    )


# spawn_treasure исполняется в reply_handlers (нужен bot+своя сессия), поэтому
# здесь только маркер: диспетчер вернёт его как отложенное действие.
SPAWN_TREASURE_SENTINEL = "__spawn_treasure__"
# Аналогично рисование: генерация+отправка фото идёт в reply_handlers (нужен bot).
DRAW_IMAGE_SENTINEL = "__draw_image__"


async def _h_draw(ctx: ToolContext) -> drun_tools.ToolResult:
    # Сам рисунок делает reply_handlers (есть bot для отправки фото). Сюда
    # прокидываем текст просьбы через meta.
    return drun_tools.ToolResult(
        ok=True, summary=DRAW_IMAGE_SENTINEL,
        meta={"request": ctx.arg_str("request", limit=400)},
    )


async def _h_spawn_treasure(ctx: ToolContext) -> drun_tools.ToolResult:
    return drun_tools.ToolResult(ok=True, summary=SPAWN_TREASURE_SENTINEL)


async def _h_create_event(ctx: ToolContext) -> drun_tools.ToolResult:
    """Запускает структурный ивент друна (challenge/prediction/mini/goal)."""
    from app.features.drun import events as drun_events

    kind_raw = ctx.arg_str("kind", "mini").lower()
    kind_map = {
        "challenge": drun_events.KIND_CHALLENGE, "челлендж": drun_events.KIND_CHALLENGE,
        "prediction": drun_events.KIND_PREDICTION, "прогноз": drun_events.KIND_PREDICTION,
        "mini": drun_events.KIND_MINI_EVENT, "мини": drun_events.KIND_MINI_EVENT,
        "goal": drun_events.KIND_GOAL, "цель": drun_events.KIND_GOAL,
    }
    kind = kind_map.get(kind_raw, drun_events.KIND_MINI_EVENT)
    title = ctx.arg_str("title", limit=256)
    if not title:
        return drun_tools.ToolResult(ok=False, error="нужен заголовок ивента")
    res = await drun_events.create_event(
        ctx.session,
        kind=kind,
        title=title,
        body=ctx.arg_str("body", limit=2000),
        created_by=ctx.owner_id,
        reward_amount=ctx.arg_int("reward", 0),
        ttl_hours=ctx.arg_int("hours", drun_events._DEFAULT_TTL_HOURS),
    )
    if not res.ok:
        human = {
            "too_many": "уже идёт максимум ивентов",
            "empty_title": "нужен заголовок ивента",
        }.get(res.error, f"не вышло создать ивент ({res.error})")
        return drun_tools.ToolResult(ok=False, error=human)
    reward = ctx.arg_int("reward", 0)
    reward_str = f", награда {reward} ешек" if reward > 0 else ""
    return drun_tools.ToolResult(
        ok=True,
        summary=f"запустил ивент #{res.event_id}: {title}{reward_str}",
        meta={"event_id": res.event_id, "kind": kind, "reward": reward},
    )


# --- Реестр: единственный источник правды ------------------------------------

REGISTRY: dict[str, ToolSpec] = {
    s.name: s for s in (
        ToolSpec(
            "grant",
            "выдать (или снять при amount<0) ешки АУДИТОРИИ",
            "amount (int), scope ('recent'|'active'|'all'|'poorest'|'richest'), "
            "limit (int, для poorest/richest — сколько игроков), minutes, days, note",
            _h_grant,
            ("дай", "выдай", "раздай", "начисли", "закинь", "накинь", "подкинь",
             "подари", "награди", "докинь", "кинь", "забери", "сними", "отними",
             "штраф", "оштрафуй", "минусани", "обнули баланс"),
        ),
        ToolSpec(
            "grant_one",
            "выдать/снять ешки ОДНОМУ игроку",
            "who (str), amount (int), note (str)",
            _h_grant_one,
        ),
        ToolSpec(
            "reset_cooldown",
            "сбросить кулдаун действия аудитории",
            "action ('farm'|'casino'|...), scope, minutes, days",
            _h_reset_cooldown,
            ("сбрось", "сбрось кд", "ресетни", "reset", "откати кд", "скинь кд"),
        ),
        ToolSpec(
            "giveaway",
            "розыгрыш призового фонда между случайными из аудитории",
            "pool (int), winners (int), scope, limit, minutes, days, note",
            _h_giveaway,
            ("разыграй", "розыгрыш", "раздача", "giveaway", "разыгровка"),
        ),
        ToolSpec(
            "mute",
            "замутить одного игрока на время",
            "who (str), minutes (int), reason (str)",
            _h_mute,
            ("замуть", "замути", "мут", "заткни"),
        ),
        ToolSpec(
            "unmute",
            "снять мут с игрока",
            "who (str)",
            _h_unmute,
            ("размуть", "размути", "сними мут", "размут"),
        ),
        ToolSpec(
            "warn",
            "выдать варн игроку (на пороге — авто-мут)",
            "who (str), reason (str)",
            _h_warn,
            ("варн", "предупрежд", "вынеси предупр", "warn"),
        ),
        ToolSpec(
            "unwarn",
            "снять все варны с игрока",
            "who (str)",
            _h_unwarn,
            ("сними варн", "убери варн", "обнули варн", "прости"),
        ),
        ToolSpec(
            "award_mmr",
            "начислить/снять MMR игроку (±1000)",
            "who (str), amount (int)",
            _h_award_mmr,
            ("ммр", "mmr", "рейтинг накинь", "накинь ммр", "срежь ммр"),
        ),
        ToolSpec(
            "ban",
            "забанить игрока (days=0 — навсегда)",
            "who (str), days (int), reason (str)",
            _h_ban,
            ("забань", "бан", "забанить", "ban", "в бан"),
        ),
        ToolSpec(
            "unban",
            "снять бан с игрока",
            "who (str)",
            _h_unban,
            ("разбань", "сними бан", "unban", "разбан"),
        ),
        ToolSpec(
            "kick",
            "кикнуть игрока из чата (краткий бан)",
            "who (str), reason (str)",
            _h_kick,
            ("кик", "кикни", "выгони", "выкини", "kick", "выпни"),
        ),
        ToolSpec(
            "web_search",
            "поискать в интернете и вернуть краткую выжимку",
            "query (str)",
            _h_websearch,
            ("погугли", "загугли", "поищи", "найди в интернете", "google",
             "что такое", "кто такой"),
        ),
        ToolSpec(
            "draw",
            "нарисовать картинку по просьбе и кинуть в чат",
            "request (str)",
            _h_draw,
            ("нарисуй", "рисуй", "набросай", "сгенери картинку", "draw",
             "изобрази", "покажи как выглядит"),
        ),
        ToolSpec(
            "grant_item",
            "выдать предмет в инвентарь игрока",
            "who (str), item (str code), quantity (int)",
            _h_grant_item,
            ("выдай предмет", "дай предмет", "подари предмет", "выдай шмот",
             "дай айтем", "выдай айтем"),
        ),
        ToolSpec(
            "multiplier",
            "глобальный множитель заработка ешек (эконом-ивент)",
            "value (float, напр. 2 = x2)",
            _h_multiplier,
            ("множитель", "x2", "х2", "удвой", "ивент", "буст"),
        ),
        ToolSpec(
            "spawn_treasure",
            "вкинуть клад (раздачу) в чат прямо сейчас",
            "{} (без аргументов)",
            _h_spawn_treasure,
            ("клад", "клады", "сокровище", "спавн", "раздай клад"),
        ),
        ToolSpec(
            "create_event",
            "запустить ивент друна (челлендж/прогноз/мини/цель) с наградой и дедлайном",
            "kind ('challenge'|'prediction'|'mini'|'goal'), title (str), "
            "body (str), reward (int ешки, ≤5000), hours (int, дедлайн)",
            _h_create_event,
            ("ивент", "запусти ивент", "сделай ивент", "челлендж", "запили ивент",
             "проведи ивент", "устрой ивент", "замути ивент", "прогноз ивент",
             "объяви ивент", "создай ивент", "ивент на"),
        ),
        ToolSpec(
            "feature_toggle",
            "включить/выключить подсистему игры (казино/бои/ферма/кейсы/магазин/подарки)",
            "feature (str: casino|duel|farm|cases|shop|gifts), enable (bool)",
            _h_feature_toggle,
            ("включи", "выключи", "вырубай", "выруби", "отключи", "врубай",
             "врубить", "врубани", "выруби казино", "выключи бои", "останови",
             "запусти", "погаси"),
        ),
        ToolSpec(
            "set_param",
            "изменить числовой параметр игры (ставки, кулдауны, множители)",
            "key (str из: casino.min_bet/casino.max_bet/casino.cooldown/"
            "duel.min_bet/duel.max_bet/duel.cooldown/farm.cooldown/farm.bonus/"
            "modifier.eshki/modifier.drop/modifier.xp), value (число)",
            _h_set_param,
            ("ставку", "ставки", "лимит ставки", "кулдаун", "кд казино",
             "кд боёв", "подними", "опусти", "понизь", "поставь множитель",
             "макс ставка", "мин ставка", "максимальную ставку"),
        ),
    )
}


def build_catalog() -> str:
    """Собирает текст каталога инструментов для системного промпта."""
    lines = []
    for spec in REGISTRY.values():
        lines.append(f"- {spec.name}: {spec.summary}.")
        lines.append(f"    args: {spec.args_doc}.")
    return "\n".join(lines)


def all_hints() -> tuple[str, ...]:
    """Все глаголы-подсказки из реестра — для дешёвого пред-фильтра."""
    hints: list[str] = []
    for spec in REGISTRY.values():
        hints.extend(spec.hints)
    return tuple(dict.fromkeys(hints))


async def dispatch(ctx: ToolContext, tool: str) -> drun_tools.ToolResult | None:
    """Исполняет тул по имени. None — если такого тула нет в реестре."""
    spec = REGISTRY.get(tool)
    if spec is None:
        return None
    return await spec.run(ctx)
