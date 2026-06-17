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

# Предел размера скачиваемой картинки (анти-DoS при следовании по url).
_MAX_IMAGE_BYTES = 12 * 1024 * 1024


def _assert_safe_public_url(raw_url: str) -> None:
    """Валидирует URL перед серверным GET — анти-SSRF.

    Картинка-эндпоинт может вернуть произвольный ``url`` в ответе; слепой GET по
    нему позволил бы увести запрос на внутренние адреса (метаданные облака,
    localhost, приватные сети). Поэтому требуем https и публичный IP. Бросает
    :class:`LlmError` при любом нарушении.
    """
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(raw_url)
    if parts.scheme != "https":
        raise LlmError("image url must be https")
    host = parts.hostname
    if not host:
        raise LlmError("image url has no host")
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise LlmError(f"image url host unresolved: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        ):
            raise LlmError("image url resolves to a non-public address")

_TIMEOUT_SECONDS = 45


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


async def vision(
    cfg: AiConfig,
    *,
    system: str,
    prompt: str,
    image_b64: str,
    media_type: str = "image/jpeg",
    model: str | None = None,
) -> str:
    """Запрос с картинкой (#9): описать/прокомментировать изображение.

    ``image_b64`` — base64 без префикса data-URI. Формирует мультимодальный
    user-ход в формате нужного провайдера (OpenAI image_url с data-URI или
    Anthropic image-block) и возвращает текст. Бросает :class:`LlmError`.
    """
    if not cfg.usable:
        raise LlmError("AI disabled or api_key/model missing")
    use_model = (model or cfg.model).strip() or cfg.model

    import aiohttp

    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            if _is_anthropic(cfg.base_url, use_model):
                content: list[dict[str, Any]] = [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                ]
                msgs = [{"role": "user", "content": content}]
                return await _anthropic_chat(http, cfg, system, msgs, use_model)
            # OpenAI-совместимый мультимодальный формат.
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{image_b64}"
                    },
                },
            ]
            msgs = [{"role": "user", "content": content}]
            return await _openai_chat(http, cfg, system, msgs, use_model)
    except aiohttp.ClientError as exc:
        raise LlmError(f"network error: {exc}") from exc


async def generate_image(cfg: AiConfig, *, prompt: str) -> bytes:
    """Генерация картинки (#10) через OpenAI images-совместимый endpoint.

    Возвращает PNG-байты. Конфиг берётся из image_* настроек (ключ — image_api_key
    с фолбэком на api_key). Бросает :class:`LlmError` при любой проблеме.
    """
    if not cfg.image_usable:
        raise LlmError("image generation disabled or misconfigured")

    import base64

    import aiohttp

    url = f"{cfg.image_base_url}/images/generations"
    key = cfg.image_api_key or cfg.api_key
    payload = {
        "model": cfg.image_model,
        "prompt": prompt[:1000],
        "n": 1,
        "size": "1024x1024",
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=120)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(url, json=payload, headers=headers) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    raise LlmError(f"HTTP {resp.status}: {_err(data)}")
    except aiohttp.ClientError as exc:
        raise LlmError(f"network error: {exc}") from exc

    try:
        item = data["data"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError(f"bad image response: {data}") from exc
    # Endpoint может вернуть base64 (b64_json) или ссылку (url).
    b64 = item.get("b64_json")
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            raise LlmError(f"bad b64 image: {exc}") from exc
    img_url = item.get("url")
    if img_url:
        # Анти-SSRF: ответ-управляемый url нельзя качать вслепую. Проверяем
        # схему/адрес, запрещаем редиректы и ограничиваем размер.
        _assert_safe_public_url(str(img_url))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.get(str(img_url), allow_redirects=False) as r2:
                    if r2.status >= 400 or r2.status in (301, 302, 303, 307, 308):
                        raise LlmError(f"image fetch HTTP {r2.status}")
                    # Читаем потоково до EOF с ограничением размера. ВАЖНО:
                    # StreamReader.read(n) отдаёт только первый доступный чанк,
                    # а не n байт — поэтому копим в цикле, иначе картинка
                    # обрезается до первого чанка.
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = await r2.content.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > _MAX_IMAGE_BYTES:
                            raise LlmError("image too large")
                        chunks.append(chunk)
                    return b"".join(chunks)
        except aiohttp.ClientError as exc:
            raise LlmError(f"image fetch error: {exc}") from exc
    raise LlmError("no image payload (b64_json/url) in response")
