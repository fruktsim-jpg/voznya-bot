"""Deterministic persona/logic critic for Drun replies.

This is intentionally cheap and safe: no tool dispatch, no economy writes, no
second LLM call. It catches obvious low-quality replies and records why the
answer was risky so future tuning can inspect assistant message metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)

_GENERIC_PHRASES = (
    "я не знаю",
    "не могу помочь",
    "как искусственный интеллект",
    "у меня нет информации",
    "ничего не могу сказать",
    "сложно сказать",
)

_ECONOMY_WORDS = (
    "ешк", "баланс", "деньг", "казино", "ставк", "банк", "богат", "бедн",
    "кошел", "монет",
)


@dataclass(frozen=True)
class Critique:
    ok: bool
    reasons: tuple[str, ...] = ()
    hint: str = ""

    def as_meta(self) -> dict[str, object]:
        return {"ok": self.ok, "reasons": list(self.reasons), "hint": self.hint}


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall((text or "").lower()) if len(w) >= 3]


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(n in low for n in needles)


def _overlap_ratio(a: str, b: str) -> float:
    ta = set(_tokens(a))
    tb = set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


def critique_response(
    *,
    query: str | None,
    context: str | None,
    response: str,
    memory_ids: list[int] | None = None,
    archive_ids: list[int] | None = None,
) -> Critique:
    reasons: list[str] = []
    text = (response or "").strip()
    query_text = query or ""
    context_text = context or ""

    if len(text) < 8:
        reasons.append("too_short")
    if len(text) > 1800:
        reasons.append("too_long")
    if _has_any(text, _GENERIC_PHRASES):
        reasons.append("generic_refusal")

    # If the user asks about concrete past/context and we retrieved archive rows,
    # the answer should share at least some topical words with the query/context.
    if archive_ids and query_text and _overlap_ratio(query_text, text) < 0.15:
        reasons.append("ignores_archive_query")

    # Economy claims are allowed only when the prompt actually included economy
    # context or the user asked about economy. This avoids confident money lore.
    if _has_any(text, _ECONOMY_WORDS):
        economy_grounded = "# ЭКОНОМИКА" in context_text or _has_any(query_text, _ECONOMY_WORDS)
        if not economy_grounded:
            reasons.append("ungrounded_economy_claim")

    # Repeating a raw memory sentence verbatim is usually worse than using it as
    # flavor. High overlap against context catches quote-dumps/generic summaries.
    if memory_ids and _overlap_ratio(context_text, text) > 0.92 and len(_tokens(text)) >= 12:
        reasons.append("context_copy")

    hint = ""
    if reasons:
        hint = (
            "Ответ выглядит слабым: сделай короче, конкретнее, по запросу; "
            "не повторяй память дословно и не утверждай экономику без блока экономики."
        )
    return Critique(ok=not reasons, reasons=tuple(reasons), hint=hint)


def repair_response_text(response: str, critique: Critique) -> str:
    """Minimal deterministic repair for obviously unsafe text.

    We do not invent facts here. For now repair only trims pathological length;
    other issues are recorded in metadata and handled by prompt/context tuning.
    """
    text = (response or "").strip()
    if "too_long" in critique.reasons:
        return text[:1400].rstrip() + "…"
    return text


def should_rewrite(critique: Critique) -> bool:
    """Whether a bad answer deserves one LLM rewrite attempt.

    Keep this conservative: the rewrite pass is for obvious quality failures,
    not for every stylistic nit. Economy issues are not rewritten automatically
    because inventing a correction would be worse than logging the risk.
    """
    if critique.ok:
        return False
    serious = {
        "too_short",
        "generic_refusal",
        "ignores_archive_query",
        "context_copy",
    }
    return any(reason in serious for reason in critique.reasons)


def rewrite_prompt(
    *,
    query: str | None,
    context: str | None,
    bad_response: str,
    critique: Critique,
) -> str:
    """Build user prompt for a single safe rewrite pass."""
    ctx = (context or "").strip()
    if len(ctx) > 5000:
        ctx = ctx[:5000].rstrip() + "…"
    return (
        "Перепиши ответ Тёмного друна.\n"
        "Требования:\n"
        "- отвечай по запросу, конкретно и живо;\n"
        "- не выдумывай факты вне контекста;\n"
        "- не повторяй память дословно;\n"
        "- не утверждай баланс/ешки/экономику, если этого нет в контексте;\n"
        "- сохрани язвительную персону, но без generic AI-отмазок;\n"
        "- верни только финальный текст ответа.\n\n"
        f"Причины критики: {', '.join(critique.reasons)}\n\n"
        f"Запрос пользователя:\n{query or ''}\n\n"
        f"Контекст:\n{ctx}\n\n"
        f"Плохой ответ:\n{bad_response}"
    )
