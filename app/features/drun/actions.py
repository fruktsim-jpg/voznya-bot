"""Парсер action-директив из ответа модели + их применение.

Друн может «попросить» экономическое действие, вставив в свой ответ директиву
особого формата на отдельной строке::

    [[econ:tax:300:за понты в чате]]
    [[econ:grant:150:по жалости, совсем нищий]]

* ``tax`` — списать (налоговая), ``grant`` — выдать (из жалости);
* число — ЖЕЛАЕМАЯ сумма (будет обрезана лимитами в ``econ.apply``);
* хвост — короткая причина «за что».

Директива относится к СОБЕСЕДНИКУ (тому, кто обратился). Из видимого текста
директива вырезается, а вместо неё ничего не подставляется — друн сам в тексте
обыгрывает выходку словами. Применение идёт только если включена власть
(``econ_enabled``) и проходят все предохранители.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.features.drun import econ
from app.features.drun.config import AiConfig

logger = get_logger(__name__)

# [[econ:tax:300:причина]] — причина опциональна. Регистронезависимо.
_DIRECTIVE_RE = re.compile(
    r"\[\[\s*econ\s*:\s*(tax|grant)\s*:\s*(\d{1,9})\s*(?::\s*([^\]]*))?\]\]",
    re.IGNORECASE,
)


@dataclass
class ParsedAction:
    """Распознанная директива (до применения)."""

    kind: str        # "tax" | "grant"
    amount: int      # запрошенная сумма
    note: str        # причина «за что»


def strip_directives(text: str) -> str:
    """Убирает все директивы из видимого текста и подчищает пробелы."""
    cleaned = _DIRECTIVE_RE.sub("", text)
    # схлопываем образовавшиеся пустые строки / двойные пробелы
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def sanitize_user_text(text: str) -> str:
    """Обезвреживает директивы в НЕДОВЕРЕННОМ вводе игрока.

    Игрок может написать в чат ``[[econ:grant:1000:...]]`` в надежде, что модель
    отзеркалит это в свой ответ и сработает самоначисление. Поэтому ЛЮБОЙ
    econ-токен в пользовательском тексте калечим (ломаем скобки), чтобы он не мог
    дойти до парсера через эхо модели. Применять к тексту игрока ДО отправки в LLM.
    """
    if not text:
        return text
    return _DIRECTIVE_RE.sub("⟦econ⟧", text)


def parse(text: str) -> ParsedAction | None:
    """Достаёт ПЕРВУЮ корректную директиву из текста (или None)."""
    m = _DIRECTIVE_RE.search(text or "")
    if not m:
        return None
    kind = m.group(1).lower()
    try:
        amount = int(m.group(2))
    except (TypeError, ValueError):
        return None
    note = (m.group(3) or "").strip()
    return ParsedAction(kind=kind, amount=amount, note=note)


async def apply_if_any(
    session: AsyncSession,
    *,
    cfg: AiConfig,
    target_id: int | None,
    text: str,
    asker_id: int | None = None,
    intent_kind: str | None = None,
) -> econ.EconResult | None:
    """Если в тексте есть директива и власть включена — применяет её.

    Возвращает результат применения (или None, если директивы нет/нет цели).
    Вырезание директивы из видимого текста — отдельным вызовом ``strip_directives``
    на стороне сервиса.

    ``intent_kind`` пробрасывается в meta транзакции (audit trail): чтобы
    по логам можно было понять, директива пришла от ROAST/HYPE-сигнала или
    модель сама решила вкатить налог без явного повода.
    """
    if not cfg.econ_enabled or target_id is None:
        return None
    action = parse(text)
    if action is None:
        return None
    result = await econ.apply(
        session,
        cfg=cfg,
        kind=action.kind,
        target_id=target_id,
        requested_amount=action.amount,
        note=action.note,
        asker_id=asker_id,
        intent_kind=intent_kind,
    )
    logger.info(
        "drun action parsed kind=%s amount=%s intent=%s applied_ok=%s reason=%s",
        action.kind, action.amount, intent_kind or "-", result.ok, result.reason,
    )
    return result
