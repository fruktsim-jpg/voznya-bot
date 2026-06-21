"""Личка владельца с друном (Phase 2): управление, диагностика, рекомендации.

Раньше друн жил только в группе (``chat_id``). Здесь — приватный канал владельца:
он пишет друну в ЛС, и друн становится оператором-помощником —

* **команды** («дай активным по 100», «забань кота») → исполняются; для
  высокоимпактных действий друн СНАЧАЛА предлагает (approval-flow, Phase 6),
  для малых/обратимых — делает сразу (в пределах капов);
* **подтверждения** («да»/«одобряю»/«нет»/«предложения»/«статус N») — управление
  очередью предложений;
* **диагностика** («что по экономике», «как дела в мире») → срез мира;
* **рекомендации** — друн сам подсказывает, что сделать (по своему worldview);
* **предпочтения** («запомни: новичков не баню») → owner-preference память;
* **болтовня** → обычный разговор в образе, но в приватном тоне оператора.

Безопасность: ВСЁ гейтится ``is_admin``. Личка недоступна не-владельцам (друн
молчит). Исполнение команд идёт тем же ``agent``/``registry`` с клампами/аудитом.
"""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logger import get_logger
from app.features.drun import agent as drun_agent
from app.features.drun import config as drun_config
from app.features.drun import owner as drun_owner
from app.features.drun import registry as drun_registry
from app.features.drun import service as drun_service
from app.features.drun import tools as drun_tools

logger = get_logger(__name__)

router = Router(name="drun_owner_dm")

_DM_CHANNEL = "owner_dm"

# Ключевые фразы управления очередью предложений (грубо, без LLM — дёшево).
_APPROVE = ("да", "одобряю", "давай", "го", "approve", "ок", "окей", "подтверждаю", "+")
_REJECT = ("нет", "отмена", "не надо", "отклоняю", "reject", "стоп", "-")
_LIST = ("предложения", "очередь", "что предлагаешь", "proposals", "пропозалы")
_DIAG = (
    "что по экономике", "как дела в мире", "диагностика", "статус мира",
    "что в мире", "сводка", "как мир", "состояние",
)
_REMEMBER_PREFIXES = ("запомни:", "запомни ", "правило:", "имей в виду:")


def _is_owner(message: Message) -> bool:
    return (
        message.from_user is not None
        and get_settings().is_admin(message.from_user.id)
    )


def _display_name(message: Message) -> str:
    u = message.from_user
    if u is None:
        return "владелец"
    return u.full_name or (f"@{u.username}" if u.username else "владелец")


def _matches(text: str, phrases: tuple[str, ...]) -> bool:
    low = text.lower().strip()
    return any(low == p or low.startswith(p + " ") or low == p for p in phrases)


async def _diagnostics(session: AsyncSession) -> str:
    """Краткий срез мира для владельца (экономика + worldview друна)."""
    from app.features.drun import economy as drun_economy
    from app.features.drun import worldview as drun_worldview

    parts: list[str] = []
    try:
        econ = await drun_economy.chat_economy_digest(session, days=7)
        if econ:
            parts.append(econ.strip())
    except Exception:  # noqa: BLE001
        logger.debug("dm diagnostics economy failed", exc_info=True)
    try:
        wv = await drun_worldview.worldview_block(session, limit=8)
        if wv:
            parts.append(wv.strip())
    except Exception:  # noqa: BLE001
        logger.debug("dm diagnostics worldview failed", exc_info=True)
    return "\n\n".join(parts)


async def _execute_tool(
    session: AsyncSession, *, owner_id: int, tool: str, args: dict
) -> drun_tools.ToolResult | None:
    """Исполняет tool через единый registry-диспетчер (клампы/аудит внутри)."""

    async def _resolve_who(who: str) -> int | None:
        return await drun_tools.find_user_id(session, who, trusted=True)

    async def _resolve_audience(*, scope, minutes, days, limit=None) -> list[int]:
        return await drun_tools.resolve_audience(
            session, scope=scope, minutes=minutes, days=days, limit=limit
        )

    ctx = drun_registry.ToolContext(
        session=session, owner_id=owner_id, args=args,
        resolve_who=_resolve_who, resolve_audience=_resolve_audience,
    )
    return await drun_registry.dispatch(ctx, tool)


@router.message(F.chat.type == ChatType.PRIVATE, (F.text | F.caption))
async def on_owner_dm(message: Message, session: AsyncSession) -> None:
    """Главный хендлер лички владельца с друном."""
    # Гейт: личка друна — только для владельца. Остальным друн в ЛС не отвечает
    # (не светим управляющий интерфейс; обычные ЛС-флоу — у других роутеров).
    if not _is_owner(message):
        return
    user = message.from_user
    if user is None:
        return

    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return

    text = (message.text or message.caption or "").strip()
    if not text:
        return
    owner_id = user.id

    # 1) Управление очередью предложений (approval-flow).
    if _matches(text, _LIST):
        await _show_proposals(message, session, owner_id)
        return

    # «да [N]» / «нет [N]» — решение по предложению (по номеру или последнему).
    decision = _parse_decision(text)
    if decision is not None:
        await _decide_proposal(message, session, owner_id, *decision)
        return

    # 2) Запоминание предпочтения владельца.
    pref = _extract_preference(text)
    if pref is not None:
        await drun_owner.remember_preference(session, owner_id=owner_id, text=pref)
        await session.commit()
        await message.answer("Запомнил. Буду так и делать.", parse_mode=None)
        return

    # 3) Диагностика мира.
    if _matches(text, _DIAG):
        diag = await _diagnostics(session)
        if not diag:
            await message.answer("Пока тихо, ничего тревожного в мире не вижу.", parse_mode=None)
            return
        # Друн пересказывает срез в образе (оператор-помощник), а не сухой дамп.
        result = await drun_service.respond(
            session,
            asker_id=owner_id,
            asker_name=_display_name(message),
            text=(
                "Дай владельцу короткую устную сводку по миру на основе этих данных "
                f"(без таблиц, по делу):\n\n{diag}"
            ),
            channel=_DM_CHANNEL,
        )
        await session.commit()
        out = result.text if result.ok and result.text else diag
        await message.answer(out, parse_mode=None)
        return

    # 4) Команда-действие? Owner пишет «дай всем по 100» / «забань кота».
    if drun_agent.looks_like_action(text):
        await _handle_command(message, session, owner_id, text)
        return

    # 5) Обычный разговор оператора с друном (рекомендации/болтовня).
    result = await drun_service.respond(
        session,
        asker_id=owner_id,
        asker_name=_display_name(message),
        text=text,
        channel=_DM_CHANNEL,
    )
    await session.commit()
    if result.ok and result.text:
        await message.answer(result.text, parse_mode=None)


# --- Команды и approval-flow -------------------------------------------------


async def _handle_command(
    message: Message, session: AsyncSession, owner_id: int, text: str
) -> None:
    """Разбирает команду владельца; высокоимпактные — через подтверждение."""
    plan = await drun_agent._plan(session, text)
    if not plan:
        # Не распознали как команду — отвечаем как на болтовню.
        result = await drun_service.respond(
            session, asker_id=owner_id, asker_name=_display_name(message),
            text=text, channel=_DM_CHANNEL,
        )
        await session.commit()
        if result.ok and result.text:
            await message.answer(result.text, parse_mode=None)
        return

    tool = str(plan.get("tool", "none")).strip().lower()
    args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
    if tool in ("none", ""):
        result = await drun_service.respond(
            session, asker_id=owner_id, asker_name=_display_name(message),
            text=text, channel=_DM_CHANNEL,
        )
        await session.commit()
        if result.ok and result.text:
            await message.answer(result.text, parse_mode=None)
        return

    # Высокоимпактное действие → предлагаем, не исполняем сразу.
    if drun_owner.is_high_impact(tool):
        proposal = await drun_owner.create_proposal(
            session, owner_id=owner_id, tool=tool, args=args,
            rationale=f"по твоей команде: «{text[:200]}»",
        )
        await session.commit()
        await message.answer(
            f"Это крупное действие ({tool}). Предложение #{proposal.id}:\n"
            f"{_describe_call(tool, args)}\n\n"
            f"Подтверди — напиши «да» (или «да {proposal.id}»), отклонить — «нет».",
            parse_mode=None,
        )
        return

    # Малое/обратимое — исполняем сразу (клампы внутри tools).
    res = await _execute_tool(session, owner_id=owner_id, tool=tool, args=args)
    await session.commit()
    if res is None:
        await message.answer("Не понял команду — переформулируй.", parse_mode=None)
        return
    await message.answer(
        (f"Сделано: {res.summary}" if res.ok else f"Не вышло: {res.error}"),
        parse_mode=None,
    )


async def _show_proposals(message: Message, session: AsyncSession, owner_id: int) -> None:
    items = await drun_owner.pending_proposals(session, owner_id=owner_id)
    await session.commit()
    if not items:
        await message.answer("Очередь предложений пуста.", parse_mode=None)
        return
    lines = ["Жду твоего решения:"]
    for p in items:
        lines.append(f"#{p.id}: {_describe_call(p.tool, p.args)} — {p.rationale}")
    lines.append("\n«да {id}» — одобрить, «нет {id}» — отклонить.")
    await message.answer("\n".join(lines), parse_mode=None)


async def _decide_proposal(
    message: Message,
    session: AsyncSession,
    owner_id: int,
    approve: bool,
    proposal_id: int | None,
) -> None:
    if proposal_id is not None:
        proposal = await drun_owner.get_proposal(session, proposal_id)
    else:
        proposal = await drun_owner.latest_pending(session, owner_id=owner_id)
    if proposal is None or proposal.status != "pending":
        await message.answer("Нет такого активного предложения.", parse_mode=None)
        return

    if not approve:
        await drun_owner.mark_decided(
            session, proposal, status="rejected", decided_by=owner_id
        )
        await session.commit()
        await message.answer(f"Ок, предложение #{proposal.id} отклонил.", parse_mode=None)
        return

    # Одобрено → исполняем тем же диспетчером.
    res = await _execute_tool(
        session, owner_id=owner_id, tool=proposal.tool, args=proposal.args or {}
    )
    if res is None:
        await drun_owner.mark_decided(
            session, proposal, status="rejected", decided_by=owner_id,
            result={"error": "dispatch_failed"},
        )
        await session.commit()
        await message.answer("Не смог исполнить — инструмент не распознан.", parse_mode=None)
        return
    await drun_owner.mark_decided(
        session, proposal, status="executed", decided_by=owner_id,
        result={"ok": res.ok, "summary": res.summary, "error": res.error},
    )
    await session.commit()
    await message.answer(
        (f"Исполнил #{proposal.id}: {res.summary}" if res.ok
         else f"Пытался исполнить #{proposal.id}, но: {res.error}"),
        parse_mode=None,
    )


# --- Парсеры/форматтеры ------------------------------------------------------


def _parse_decision(text: str) -> tuple[bool, int | None] | None:
    """«да»/«нет»/«ок N»/«отмена»/«не надо» → (approve, id|None). None — не решение.

    Слова решения берутся ИЗ ``_APPROVE``/``_REJECT`` (единый источник), а не
    хардкодятся здесь — иначе «ок/давай/го/отмена/стоп/не надо» молча
    игнорировались бы. Поддерживает многословные фразы и хвостовой номер
    предложения (в т.ч. слитный «+5»).
    """
    low = text.lower().strip()
    if not low:
        return None
    # Отделяем необязательный хвостовой номер предложения от фразы-решения.
    m = re.match(r"^(.*?)\s*(\d+)?$", low)
    phrase = (m.group(1) if m else low).strip()
    pid = int(m.group(2)) if (m and m.group(2)) else None
    if phrase in _APPROVE:
        return True, pid
    if phrase in _REJECT:
        return False, pid
    return None


def _extract_preference(text: str) -> str | None:
    """Достаёт предпочтение из «запомни: ...» / «правило: ...»."""
    low = text.lower()
    for pref in _REMEMBER_PREFIXES:
        if low.startswith(pref):
            rest = text[len(pref):].strip()
            return rest or None
    return None


def _describe_call(tool: str, args: dict) -> str:
    """Человекочитаемое описание tool-вызова для подтверждения."""
    if not args:
        return tool
    parts = ", ".join(f"{k}={v}" for k, v in args.items() if v not in (None, ""))
    return f"{tool}({parts})" if parts else tool
