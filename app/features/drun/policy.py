"""Детерминированная поведенческая политика Друна перед LLM.

Промпт и память дают характер, но часть решений должна быть жёсткой логикой:
новичка не давить старым лором, после недавней econ-выходки не трогать ешки,
в горячем чате отвечать короче, а при прямом вопросе сначала отвечать.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiProfile, User
from app.services import cooldowns
from app.features.drun import memory as drun_memory

_ECON_ACTION = "drun_econ"


@dataclass(frozen=True)
class BehaviorPolicy:
    lines: list[str] = field(default_factory=list)
    allow_econ_hint: bool = True

    def block(self) -> str:
        if not self.lines:
            return ""
        body = "\n".join(f"- {line}" for line in self.lines)
        return f"# ПОВЕДЕНЧЕСКАЯ ПОЛИТИКА НА ЭТУ РЕПЛИКУ\n{body}"


def build_policy_from_signals(
    *,
    messages_count: int = 0,
    affinity_score: int = 0,
    opinion_annoyance: float = 50.0,
    chat_heat: int = 0,
    recent_econ_remaining: float = 0.0,
    intent_kind: str | None = None,
    addressed: bool = True,
    has_question: bool = False,
    has_relationships: bool = False,
) -> BehaviorPolicy:
    """Pure policy rules from already-known signals."""
    lines: list[str] = []
    allow_econ = True

    if messages_count < 20:
        lines.append(
            "человек для тебя почти новый: не дави старым лором и не делай вид, "
            "что знаешь его глубоко; лучше присмотрись и отвечай по текущей реплике"
        )
    if recent_econ_remaining > 0:
        allow_econ = False
        lines.append(
            "ешки этого игрока недавно уже трогали: НЕ обещай и НЕ проси новую "
            "[[econ:...]] директиву, даже если хочется подколоть"
        )
    if chat_heat >= 8:
        lines.append("чат горячий: отвечай короче, одним ударом, без монолога и лекции")
    elif chat_heat <= 1 and not addressed:
        lines.append("чат тихий: если вкидываешься, дай тему/крючок для ответа, а не отчёт")
    if has_question and addressed:
        lines.append("сначала ответь на прямой вопрос, и только потом подкалывай")
    if affinity_score <= -35 or opinion_annoyance >= 72:
        lines.append("отношение к человеку напряжённое: можно быть острее, но не путай факт и наезд")
    elif affinity_score >= 35:
        lines.append("это скорее свой: можно тепло подъебнуть, не превращая ответ в сюсюканье")
    if has_relationships:
        lines.append("если в контексте есть брак/соперники/кореша — можно использовать одну такую связь как живую деталь")
    if intent_kind in {"support", "hype"}:
        lines.append("если вспоминаешь старую память, бери одну конкретную деталь, не список")

    return BehaviorPolicy(lines=lines, allow_econ_hint=allow_econ)


async def build_policy(
    session: AsyncSession,
    *,
    subject_id: int | None,
    channel: str,
    intent_kind: str | None = None,
    addressed: bool = True,
    text: str = "",
) -> BehaviorPolicy:
    """Best-effort policy builder. Any DB failure degrades to empty policy."""
    if subject_id is None:
        heat = 0
        try:
            heat = await drun_memory.recent_chat_count(session, channel=channel, seconds=180)
        except Exception:  # noqa: BLE001
            pass
        return build_policy_from_signals(chat_heat=heat, intent_kind=intent_kind, addressed=addressed)

    messages_count = 0
    affinity_score = 0
    annoyance = 50.0
    has_relationships = False
    heat = 0
    econ_remaining = 0.0
    try:
        user = await session.get(User, subject_id)
        messages_count = int(getattr(user, "messages_count", 0) or 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        prof = await session.get(AiProfile, subject_id)
        if prof is not None:
            data = prof.data or {}
            affinity_score = int(((data.get("affinity") or {}).get("score", 0)) or 0)
            annoyance = float(((data.get("opinion") or {}).get("annoyance", 50)) or 50)
            has_relationships = bool(data.get("relationships"))
    except Exception:  # noqa: BLE001
        pass
    try:
        heat = await drun_memory.recent_chat_count(session, channel=channel, seconds=180)
    except Exception:  # noqa: BLE001
        pass
    try:
        econ_remaining = await cooldowns.get_remaining(session, subject_id, _ECON_ACTION)
    except Exception:  # noqa: BLE001
        pass
    has_question = "?" in (text or "") or any(w in (text or "").lower() for w in ("как", "что", "кто", "где", "почему", "зачем"))
    return build_policy_from_signals(
        messages_count=messages_count,
        affinity_score=affinity_score,
        opinion_annoyance=annoyance,
        chat_heat=heat,
        recent_econ_remaining=econ_remaining,
        intent_kind=intent_kind,
        addressed=addressed,
        has_question=has_question,
        has_relationships=has_relationships,
    )
