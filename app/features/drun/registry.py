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
    resolve_audience: Callable[..., Awaitable[list[int]]]

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


# --- Реестр: единственный источник правды ------------------------------------

REGISTRY: dict[str, ToolSpec] = {
    s.name: s for s in (
        ToolSpec(
            "grant",
            "выдать (или снять при amount<0) ешки АУДИТОРИИ",
            "amount (int), scope ('recent'|'active'|'all'), minutes, days, note",
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
            "pool (int), winners (int), scope, minutes, days, note",
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
