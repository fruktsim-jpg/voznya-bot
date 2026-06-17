"""Веб-доступ друна (#11): поиск в интернете по запросу.

Друн иногда должен знать что-то из реального мира («что за мем», «кто это»,
«что вышло») — без интернета он застывает в дате обучения. Здесь — тонкий,
безопасный и ОПЦИОНАЛЬНЫЙ слой:

* выключен по умолчанию (``web_enabled``); работает только если оператор задал
  ``web_search_url`` (SearXNG или совместимый JSON-endpoint ``?q=&format=json``);
* дневной кап запросов (``web_daily_cap``) — анти-абуз и анти-расход;
* возвращает короткую выжимку (топ-N заголовков + сниппетов), которую друн
  подмешивает в ответ; НЕ исполняет страницы, НЕ ходит по произвольным URL.

Любой сбой — пустой результат, друн просто не использует веб.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun import config as drun_config
from app.models import AiMessage

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 12
_MAX_RESULTS = 5
# Канал-маркер веб-запросов в ai_messages — для дневного капа (role='websearch').
_WEB_ROLE = "websearch"


@dataclass
class WebResult:
    """Результат веб-поиска: краткая выжимка для подмешивания в контекст."""

    ok: bool
    query: str = ""
    summary: str = ""
    items: list[dict] = field(default_factory=list)
    error: str = ""


async def _count_today(session: AsyncSession) -> int:
    """Сколько веб-запросов сделано за последние сутки (дневной кап)."""
    since = now_utc() - timedelta(days=1)
    total = await session.scalar(
        select(func.count()).select_from(AiMessage)
        .where(AiMessage.role == _WEB_ROLE)
        .where(AiMessage.created_at >= since)
    )
    return int(total or 0)


async def search(session: AsyncSession, query: str) -> WebResult:
    """Ищет в интернете через настроенный JSON-endpoint. Коммит — на вызывающем.

    Возвращает :class:`WebResult` с краткой выжимкой. Никогда не бросает —
    при любой проблеме отдаёт ``ok=False`` и друн просто игнорит веб.
    """
    cfg = await drun_config.get_config(session)
    if not cfg.web_usable:
        return WebResult(ok=False, error="disabled")

    q = (query or "").strip()
    if not q or len(q) > 300:
        return WebResult(ok=False, error="bad query")

    if await _count_today(session) >= cfg.web_daily_cap:
        logger.debug("websearch: daily cap reached")
        return WebResult(ok=False, error="cap")

    import aiohttp

    params = {"q": q, "format": "json"}
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(cfg.web_search_url, params=params) as resp:
                if resp.status >= 400:
                    return WebResult(ok=False, error=f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("websearch request failed: %s", exc)
        return WebResult(ok=False, error="network")

    items = _extract(data)
    if not items:
        return WebResult(ok=False, query=q, error="empty")

    # Учитываем запрос для дневного капа (маркер в ai_messages).
    session.add(AiMessage(role=_WEB_ROLE, content=q[:300], channel="web"))

    lines = [f"- {it['title']}: {it['snippet']}" for it in items if it["snippet"]]
    summary = "\n".join(lines[:_MAX_RESULTS])
    return WebResult(ok=True, query=q, summary=summary, items=items)


def _extract(data: object) -> list[dict]:
    """Достаёт топ-результаты из ответа SearXNG-совместимого endpoint."""
    if not isinstance(data, dict):
        return []
    raw = data.get("results")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for el in raw[: _MAX_RESULTS * 2]:
        if not isinstance(el, dict):
            continue
        title = str(el.get("title", "")).strip()[:160]
        snippet = str(el.get("content", "") or el.get("snippet", "")).strip()[:280]
        url = str(el.get("url", "")).strip()[:400]
        if title:
            out.append({"title": title, "snippet": snippet, "url": url})
        if len(out) >= _MAX_RESULTS:
            break
    return out