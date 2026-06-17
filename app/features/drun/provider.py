"""LLM-провайдер друна. OpenAI-совместимый + ветка Anthropic (Claude).

Один интерфейс :func:`chat` поверх HTTP (aiohttp уже в зависимостях через
aiogram — новый пакет не нужен). Провайдер определяется по ``base_url``:

* содержит ``anthropic`` → Anthropic Messages API (Claude);
* иначе → OpenAI-совместимый ``/chat/completions`` (OpenAI, OpenRouter, любой
  совместимый endpoint).

Конфиг (base_url/api_key/model/temperature/max_tokens) приходит из ``ai_settings``
через :class:`AiConfig`. Ошибки сети/HTTP оборачиваются в :class:`LlmError`.
"""

from __future__ import annotations

from typing import Any

from app.core.logger import get_logger
from app.features.drun.config import AiConfig

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 30


class LlmError(RuntimeError):
    """Ошибка обращения к модели (сеть/HTTP/формат ответа)."""


def _is_anthropic(base_url: str, model: str) -> bool:
    """Anthropic-формат (``/v1/messages``) определяем по URL ИЛИ по модели.

    Многие прокси/шлюзы отдают Claude по адресу без слова «anthropic», но сам
    запрос всё равно обязан идти в Anthropic-формате — модель ``claude-*``
    доступна только через ``/v1/messages``. Поэтому ориентируемся ещё и на имя
    модели.
    """
    return "anthropic" in base_url.lower() or "claude" in model.lower()


async def chat(
    cfg: AiConfig,
    *,
    system: str,
    messages: list[dict[str, str]],
    model: str | None = None,
) -> str:
    """Один запрос к модели. ``messages`` — список {role, content} (user/assistant).

    ``model`` переопределяет модель из конфига (например быстрая модель для
    служебных задач). Возвращает текст ответа. Бросает :class:`LlmError`.
    """
    if not cfg.usable:
        raise LlmError("AI disabled or api_key/model missing")

    use_model = (model or cfg.model).strip() or cfg.model

    # aiohttp поставляется вместе с aiogram (рантайм-зависимость). Импорт
    # ленивый, чтобы модуль импортировался даже там, где aiohttp не установлен
    # (например, в окружении прогона юнит-тестов без сетевых зависимостей).
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            if _is_anthropic(cfg.base_url, use_model):
                return await _anthropic_chat(http, cfg, system, messages, use_model)
            return await _openai_chat(http, cfg, system, messages, use_model)
    except aiohttp.ClientError as exc:
        raise LlmError(f"network error: {exc}") from exc


async def _openai_chat(
    http: Any,
    cfg: AiConfig,
    system: str,
    messages: list[dict[str, str]],
    model: str,
) -> str:
    url = f"{cfg.base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "messages": [{"role": "system", "content": system}, *messages],
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    async with http.post(url, json=payload, headers=headers) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            raise LlmError(f"HTTP {resp.status}: {_err(data)}")
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"bad response shape: {data}") from exc


async def _anthropic_chat(
    http: Any,
    cfg: AiConfig,
    system: str,
    messages: list[dict[str, str]],
    model: str,
) -> str:
    url = f"{cfg.base_url}/messages"
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": cfg.max_tokens,
        "temperature": cfg.temperature,
        "system": system,
        "messages": messages,
    }
    headers = {
        "x-api-key": cfg.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    async with http.post(url, json=payload, headers=headers) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            raise LlmError(f"HTTP {resp.status}: {_err(data)}")
        try:
            # Модель может вернуть несколько блоков (thinking + text при
            # extended thinking). Берём первый блок типа "text", а не [0].
            blocks = data.get("content") or []
            text = next(
                (b.get("text", "") for b in blocks if b.get("type") == "text"),
                "",
            )
            if not text and blocks:
                # Фолбэк: вдруг блок без type, но с text.
                text = blocks[0].get("text", "")
            if not text:
                raise KeyError("no text block")
            return text.strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LlmError(f"bad response shape: {data}") from exc


def _err(data: Any) -> str:
    """Достаёт человекочитаемую ошибку из тела ответа провайдера."""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)
        if err:
            return str(err)
    return str(data)[:300]
