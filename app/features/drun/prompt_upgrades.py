"""Безопасные апгрейды DB-промптов Друна.

Продовые `ai_prompts` могут перекрывать дефолты из кода, поэтому улучшения
поведения нужно уметь докатить в базу отдельно. Этот модуль добавляет короткие
идемпотентные блоки: повторный запуск ничего не дублирует.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.drun import config as drun_config
from app.models import AiPrompt


@dataclass(frozen=True)
class PromptPatch:
    name: str
    marker: str
    block: str


PATCHES: tuple[PromptPatch, ...] = (
    PromptPatch(
        name=drun_config.PROMPT_REPLY,
        marker="DRUN_SMART_REPLY_V1",
        block=(
            "\n\n# DRUN_SMART_REPLY_V1\n"
            "Приоритет ответа: сначала прямой смысл сообщения; затем личная "
            "память/отношение/реплай-нить; затем общая атмосфера; и только потом "
            "статы или ешки, если они реально в тему. Если есть конкретная память "
            "о человеке — используй максимум одну деталь как живой укол/намёк, "
            "не пересказывай досье. Люди и история важнее балансов."
        ),
    ),
    PromptPatch(
        name=drun_config.PROMPT_OBSERVATION,
        marker="DRUN_SMART_OBSERVE_V1",
        block=(
            "\n\n# DRUN_SMART_OBSERVE_V1\n"
            "Не влезай просто ради шума. Хороший вкид цепляется за конкретного "
            "человека, старую память, конфликт, тишину, странный паттерн или "
            "легенду. Если зацепки нет — лучше молчи, чем лей универсальный шум."
        ),
    ),
    PromptPatch(
        name=drun_config.PROMPT_REACTION,
        marker="DRUN_SMART_REACTION_V1",
        block=(
            "\n\n# DRUN_SMART_REACTION_V1\n"
            "Реагируй как участник чата, не как диктор. Не пересказывай событие "
            "из лога: добавь отношение, кто красавчик, кто опозорился, кому это "
            "припомнить потом. Слабое событие — короткий укол или молчание лучше "
            "пафоса."
        ),
    ),
    PromptPatch(
        name=drun_config.PROMPT_PERSONA,
        marker="DRUN_ACTION_HONESTY_V1",
        block=(
            "\n\n# DRUN_ACTION_HONESTY_V1\n"
            "Никогда не утверждай, что реально выдал/снял/начислил/отобрал ешки, "
            "если в этом же ответе нет соответствующей [[econ:...]] директивы. "
            "Если операция может быть заблокирована лимитом или кулдауном — не "
            "обещай конкретный итог слишком уверенно; система сама допишет факт "
            "реального движения денег."
        ),
    ),
)


def apply_patch_to_body(body: str, patch: PromptPatch) -> tuple[str, bool]:
    """Pure/idempotent body patch."""
    if patch.marker in (body or ""):
        return body, False
    base = (body or "").rstrip()
    return f"{base}{patch.block}\n", True


async def apply_prompt_upgrades(session: AsyncSession, *, dry_run: bool = True) -> dict[str, int]:
    """Добавляет поведенческие блоки в существующие DB-промпты."""
    stats = {"seen": 0, "changed": 0, "missing": 0}
    for patch in PATCHES:
        stats["seen"] += 1
        row = await session.scalar(select(AiPrompt).where(AiPrompt.name == patch.name))
        if row is None:
            stats["missing"] += 1
            continue
        new_body, changed = apply_patch_to_body(row.body, patch)
        if not changed:
            continue
        stats["changed"] += 1
        if not dry_run:
            row.body = new_body
    if not dry_run and stats["changed"]:
        drun_config.invalidate_cache()
    return stats
