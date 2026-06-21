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

import html
import re
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun import config as drun_config
from app.models import AiMessage

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 12
_MAX_RESULTS = 5
_BROWSE_RESULTS = 3
_MAX_PAGE_BYTES = 1_000_000
_MAX_PAGE_SNIPPET = 700
# Канал-маркер веб-запросов в ai_messages — для дневного капа (role='websearch').
_WEB_ROLE = "websearch"
_WEATHER_HOST = "https://wttr.in"


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

    # Погода через общий SearXNG часто даёт SEO-мусор или вообще другой город
    # (например, Amsterdam → Санкт-Петербург из-за локали источников). Для этого
    # класса вопросов сначала идём в фиксированный weather endpoint, а обычный
    # поиск оставляем фолбэком.
    weather = await _weather_search(session, q)
    if weather.ok:
        return weather

    import aiohttp

    params = {"q": _rewrite_query(q), "format": "json"}
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(cfg.web_search_url, params=params) as resp:
                if resp.status >= 400:
                    return WebResult(ok=False, error=f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
            items = _extract(data)
            items = await _browse_items(http, items)
    except Exception as exc:  # noqa: BLE001
        logger.debug("websearch request failed: %s", exc)
        return WebResult(ok=False, error="network")

    if not items:
        return WebResult(ok=False, query=q, error="empty")

    # Учитываем запрос для дневного капа (маркер в ai_messages).
    session.add(AiMessage(role=_WEB_ROLE, content=q[:300], channel="web"))

    lines = [
        f"- {it['title']} ({_host(it.get('url', ''))}): {it['snippet']}"
        for it in items if it["snippet"]
    ]
    summary = "\n".join(lines[:_MAX_RESULTS])
    return WebResult(ok=True, query=q, summary=summary, items=items)


def _rewrite_query(query: str) -> str:
    """Улучшает поисковый запрос, не меняя смысл.

    SearXNG на короткие русские запросы часто отдаёт агрегаторный мусор. Чуть
    расширяем классы свежих вопросов, чтобы поисковик выбирал актуальные страницы,
    а не старые SEO-лендинги.
    """
    q = (query or "").strip()
    low = q.lower()
    if any(h in low for h in ("новости", "что случилось", "что произошло")):
        return f"{q} сегодня"
    if any(h in low for h in ("курс", "сколько стоит", "цена")):
        return f"{q} сейчас"
    return q


async def _browse_items(http, items: list[dict]) -> list[dict]:
    """Best-effort browse top search results and replace weak snippets.

    Это делает интернет друна не просто «поиском заголовков», а минимальным
    браузером: открыть первые HTTPS-результаты, вытащить title/meta/текст и дать
    модели более плотный контекст. Безопасность: только публичный HTTPS URL,
    небольшой лимит тела, без JS, без редиректов.
    """
    out: list[dict] = []
    browsed = 0
    for item in items:
        enriched = dict(item)
        url = enriched.get("url", "")
        if browsed < _BROWSE_RESULTS and _safe_browse_url(url):
            page = await _fetch_page_summary(http, url)
            if page:
                # Сохраняем исходный заголовок поисковика, если страница не дала
                # свой. Сниппет страницы обычно точнее, чем search content.
                enriched["title"] = page.get("title") or enriched.get("title", "")
                enriched["snippet"] = page.get("snippet") or enriched.get("snippet", "")
                enriched["browsed"] = True
                browsed += 1
        out.append(enriched)
    return out


def _safe_browse_url(raw_url: str) -> bool:
    """Разрешаем browse только HTTPS URL с публичным DNS-резолвом."""
    try:
        from app.features.drun import provider as drun_provider

        drun_provider._assert_safe_public_url(raw_url)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _fetch_page_summary(http, raw_url: str) -> dict | None:
    """Скачивает HTML-страницу и возвращает компактный title/snippet."""
    try:
        async with http.get(raw_url, allow_redirects=False) as resp:
            ctype = str(resp.headers.get("content-type", "")).lower()
            if resp.status >= 400 or resp.status in (301, 302, 303, 307, 308):
                return None
            if "text/html" not in ctype and "text/plain" not in ctype:
                return None
            raw = await resp.content.read(_MAX_PAGE_BYTES + 1)
            if len(raw) > _MAX_PAGE_BYTES:
                return None
            charset = resp.charset or "utf-8"
            text = raw.decode(charset, errors="ignore")
    except Exception:  # noqa: BLE001
        return None
    title, snippet = _html_to_summary(text)
    if not title and not snippet:
        return None
    return {"title": title, "snippet": snippet}


def _html_to_summary(markup: str) -> tuple[str, str]:
    """Очень простой extractor: title + meta description + видимый текст."""
    src = markup or ""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", src, flags=re.I | re.S)
    if m:
        title = _squash(html.unescape(_strip_tags(m.group(1))))[:160]
    meta = ""
    m = re.search(
        r"<meta[^>]+(?:name|property)=[\"'](?:description|og:description)[\"'][^>]+content=[\"'](.*?)[\"']",
        src,
        flags=re.I | re.S,
    )
    if not m:
        m = re.search(
            r"<meta[^>]+content=[\"'](.*?)[\"'][^>]+(?:name|property)=[\"'](?:description|og:description)[\"']",
            src,
            flags=re.I | re.S,
        )
    if m:
        meta = _squash(html.unescape(_strip_tags(m.group(1))))

    clean = re.sub(r"<script\b.*?</script>", " ", src, flags=re.I | re.S)
    clean = re.sub(r"<style\b.*?</style>", " ", clean, flags=re.I | re.S)
    clean = re.sub(r"<noscript\b.*?</noscript>", " ", clean, flags=re.I | re.S)
    text = _squash(html.unescape(_strip_tags(clean)))
    # Убираем совсем короткий boilerplate вокруг навигации.
    chunks = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if len(p.strip()) >= 60]
    body = " ".join(chunks[:4]) if chunks else text
    snippet = _squash(" ".join(p for p in (meta, body) if p))[:_MAX_PAGE_SNIPPET]
    return title, snippet


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _host(raw_url: str) -> str:
    try:
        return urlsplit(raw_url).hostname or "source"
    except Exception:  # noqa: BLE001
        return "source"


async def _weather_search(session: AsyncSession, query: str) -> WebResult:
    """Точный погодный путь для «погода в X» через wttr.in.

    Это не произвольный URL и не браузинг страниц: фиксированный публичный
    endpoint, JSON, короткая выжимка. Если город не вытащили или endpoint не
    ответил — возвращаем ok=False, и caller уходит в обычный web search.
    """
    location = _extract_weather_location(query)
    if not location:
        return WebResult(ok=False, error="not_weather")

    import aiohttp

    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(
                f"{_WEATHER_HOST}/{location}",
                params={"format": "j1", "lang": "ru"},
            ) as resp:
                if resp.status >= 400:
                    return WebResult(ok=False, query=query, error=f"weather HTTP {resp.status}")
                data = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("weather request failed: %s", exc)
        return WebResult(ok=False, query=query, error="weather network")

    summary = _format_weather(data, location)
    if not summary:
        return WebResult(ok=False, query=query, error="weather empty")

    # Учитываем погодный запрос в том же дневном капе, что и web search.
    session.add(AiMessage(role=_WEB_ROLE, content=query[:300], channel="web"))
    return WebResult(
        ok=True,
        query=query,
        summary=summary,
        items=[{"title": f"Погода: {location}", "snippet": summary, "url": _WEATHER_HOST}],
    )


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


_WEATHER_RE = re.compile(
    r"(?:погод[аыуе]?|температур[аыуе]?|градус(?:ы|ов)?)"
    r"(?:\s+(?:сейчас|сегодня|завтра|на\s+сегодня|на\s+завтра))?"
    r"(?:\s+(?:в|во|на)\s+(.+?))?\s*$",
    re.IGNORECASE,
)


def _extract_weather_location(text: str) -> str:
    """Достаёт город из погодного вопроса. Пусто — не погодный запрос."""
    low = (text or "").strip().lower()
    if not any(h in low for h in ("погод", "температур", "градус")):
        return ""
    match = _WEATHER_RE.search(text or "")
    loc = (match.group(1) if match and match.group(1) else "").strip()
    if not loc:
        # «какая погода?» без города: wttr.in сам определит по IP контейнера, это
        # обычно хуже явного города, но всё равно лучше SEO-мусора.
        loc = ""
    loc = re.sub(r"[?!.,;:]+$", "", loc).strip()
    loc = re.sub(r"\b(сейчас|сегодня|завтра|пожалуйста|плиз)\b", "", loc, flags=re.I).strip()
    # Разрешаем буквы/цифры/пробел/дефис: это пойдёт только как path-сегмент к
    # фиксированному host wttr.in, но всё равно чистим мусор.
    loc = "".join(ch for ch in loc[:80] if ch.isalnum() or ch in " -_()").strip()
    return loc


def _format_weather(data: object, location: str) -> str:
    """Короткая выжимка из wttr.in j1 JSON."""
    if not isinstance(data, dict):
        return ""
    current = (data.get("current_condition") or [{}])[0]
    weather = (data.get("weather") or [{}])[0]
    if not isinstance(current, dict):
        return ""
    temp = current.get("temp_C")
    feels = current.get("FeelsLikeC")
    humidity = current.get("humidity")
    wind = current.get("windspeedKmph")
    desc_items = current.get("lang_ru") or current.get("weatherDesc") or []
    desc = ""
    if desc_items and isinstance(desc_items, list) and isinstance(desc_items[0], dict):
        desc = str(desc_items[0].get("value") or "").strip()
    chance = ""
    if isinstance(weather, dict):
        hourly = weather.get("hourly") or []
        if hourly and isinstance(hourly[0], dict):
            chance = str(hourly[0].get("chanceofrain") or "").strip()
    place = location or "текущая локация"
    parts = [f"{place}: {temp}°C" if temp not in (None, "") else f"{place}: погода"]
    if feels not in (None, ""):
        parts.append(f"ощущается как {feels}°C")
    if desc:
        parts.append(desc.lower())
    if humidity not in (None, ""):
        parts.append(f"влажность {humidity}%")
    if wind not in (None, ""):
        parts.append(f"ветер {wind} км/ч")
    if chance:
        parts.append(f"шанс дождя {chance}%")
    return ", ".join(parts) + "."


# --- Авто-поиск для фактических вопросов (#3: погода/новости/факты) -----------

# Маркеры запроса, который требует РЕАЛЬНЫХ данных из мира, а не выдумки друна.
# Друн не знает погоду/курс/новости из головы — без веба он галлюцинирует.
_FACTUAL_HINTS = (
    "погода", "погод", "температур", "градус", "дождь", "снег", "прогноз",
    "курс", "доллар", "евро", "биткоин", "крипт", "акци",
    "новости", "что случилось", "что произошло",
    "кто такой", "кто такая", "что такое", "что за",
    "когда выйдет", "когда вышел", "сколько стоит", "цена на",
    "счёт", "матч", "результат игры", "кто выиграл",
)
# Если это явно про внутренний мир Возни — НЕ лезем в интернет (там свои данные).
_INTERNAL_HINTS = (
    "ешк", "ммр", "mmr", "дуэл", "ферм", "казино", "клад", "баланс",
    "репутаци", "возн", "чат", "пидор",
)


def looks_factual(text: str) -> bool:
    """Похоже ли на вопрос, требующий реальных данных из интернета (а не лора)."""
    low = (text or "").lower()
    if any(h in low for h in _INTERNAL_HINTS):
        return False
    return any(h in low for h in _FACTUAL_HINTS)


async def auto_context(session: AsyncSession, text: str) -> str:
    """Если реплика — фактический вопрос, тянет веб-выжимку для подмешивания.

    Возвращает готовый контекст-блок (или пусто). Это делает ответы на «какая
    погода в Москве», «что за мем X», «сколько стоит Y» основанными на реальных
    данных, а не на выдумке. Любой сбой/выключенный веб → пустая строка.
    """
    if not looks_factual(text):
        return ""
    cfg = await drun_config.get_config(session)
    if not cfg.web_usable:
        return ""
    res = await search(session, (text or "").strip()[:300])
    if not res.ok or not res.summary:
        return ""
    return (
        "# ДАННЫЕ ИЗ ИНТЕРНЕТА (свежие, по вопросу собеседника — опирайся на "
        "них, не выдумывай; перескажи СВОИМИ словами в своём стиле, без ссылок "
        "и без занудного списка):\n" + res.summary
    )
