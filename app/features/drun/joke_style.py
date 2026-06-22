"""Joke form/style rotation for Drun.

Joke material gives premises; style gives the comedic vessel. Without a style
selector the model quickly collapses into one-liners. This module picks a fresh
format and renders a compact prompt block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiMessage

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


@dataclass(frozen=True)
class JokeStyle:
    key: str
    title: str
    instruction: str
    format_hint: str


_STYLES: tuple[JokeStyle, ...] = (
    JokeStyle(
        key="anecdote",
        title="анекдот",
        instruction="Сделай классический мини-анекдот: бытовой сетап, потом резкий локальный панч.",
        format_hint="2-4 короткие реплики/строки; последняя строка — панчлайн.",
    ),
    JokeStyle(
        key="police_report",
        title="ментовской протокол",
        instruction="Пиши как протокол задержания абсурда: сухой официальный тон + нелепая причина.",
        format_hint="1 строка 'Протокол:', 1-2 пункта обвинения, финальный панч.",
    ),
    JokeStyle(
        key="therapy_diagnosis",
        title="психологический диагноз",
        instruction="Поставь выдуманный диагноз чату/герою как грубый, но смешной псевдопсихолог.",
        format_hint="Диагноз + симптом + нелепое лечение. Не выдавай за настоящую медицину.",
    ),
    JokeStyle(
        key="news_report",
        title="новостная сводка",
        instruction="Сделай новость из мира VOZNYA: серьёзная подача, тупейший локальный повод.",
        format_hint="Заголовок + 1 предложение новости + короткий панч.",
    ),
    JokeStyle(
        key="ad_copy",
        title="реклама/объявление",
        instruction="Преврати материал в нелепую рекламу товара/услуги для жителей VOZNYA.",
        format_hint="Слоган + что продаём + почему это позорно смешно.",
    ),
    JokeStyle(
        key="manual",
        title="инструкция/гайд",
        instruction="Сделай фейковую инструкцию, где каждый шаг становится всё абсурднее.",
        format_hint="3 коротких шага; третий — панчлайн.",
    ),
    JokeStyle(
        key="myth",
        title="миф/легенда",
        instruction="Расскажи как древний миф VOZNYA: пафосно, но про максимально мелкую чушь.",
        format_hint="1 пафосный сетап + 1 приземляющий панч.",
    ),
    JokeStyle(
        key="dialogue",
        title="диалог дегенератов",
        instruction="Сделай короткий диалог двух персонажей, где второй ломает ожидание.",
        format_hint="2-4 реплики, без объяснения шутки.",
    ),
)


def available_styles() -> tuple[JokeStyle, ...]:
    return _STYLES


def _topic_seed(text: str) -> int:
    toks = _WORD_RE.findall((text or "").lower())
    return sum(sum(ord(ch) for ch in tok) for tok in toks) + len(toks) * 17


def select_joke_style(query: str, *, recent_styles: list[str] | None = None) -> JokeStyle:
    """Pick a deterministic-but-rotating style, avoiding recent style keys."""
    recent = set(recent_styles or [])
    styles = [s for s in _STYLES if s.key not in recent]
    if not styles:
        styles = list(_STYLES)
    idx = _topic_seed(query) % len(styles)
    return styles[idx]


def render_joke_style(style: JokeStyle) -> str:
    return (
        "# ФОРМА ШУТКИ\n"
        f"Выбран стиль: {style.title} ({style.key}).\n"
        f"Как писать: {style.instruction}\n"
        f"Формат: {style.format_hint}\n"
        "Не объясняй шутку после панчлайна. Не скатывайся в ешки/дуэли/КД, "
        "если пользователь сам этого не просил."
    )


async def recent_joke_styles(
    session: AsyncSession,
    *,
    channel: str = "chat",
    limit: int = 12,
) -> list[str]:
    rows = (
        await session.execute(
            select(AiMessage.meta)
            .where(AiMessage.channel == channel)
            .where(AiMessage.role == "assistant")
            .order_by(AiMessage.created_at.desc())
            .limit(limit)
        )
    ).all()
    out: list[str] = []
    seen: set[str] = set()
    for (meta,) in rows:
        style = (meta or {}).get("joke_style")
        if isinstance(style, str) and style and style not in seen:
            seen.add(style)
            out.append(style)
    return out


async def build_joke_style_block(
    session: AsyncSession,
    *,
    query: str,
    channel: str = "chat",
) -> tuple[str, str]:
    style = select_joke_style(
        query,
        recent_styles=await recent_joke_styles(session, channel=channel),
    )
    return render_joke_style(style), style.key
