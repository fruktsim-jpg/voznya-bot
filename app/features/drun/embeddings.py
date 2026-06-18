"""Локальные embeddings для долгой памяти друна (LEAP-1).

Зачем: BM25/trigram (миграция 0049) ловят точные токены и опечатки, но
пасуют на синонимах/перифразах: «слил всё в казике» ↔ «казино его обнулило»
для FTS — разные слова. Семантические вектора закрывают этот пробел —
cosine-сходство таких пар стабильно > 0.8.

Почему self-hosted, а не gateway:
* wellflow gateway отдаёт 501 на /v1/embeddings для всех моделей (проверено
  18.06.2026 на всех 7 доступных id) — embeddings там не реализованы;
* прямой OpenAI требовал бы второго платёжного канала и сетевой зависимости;
* fastembed + multilingual-e5-small — бесплатно, без сети, нулевая
  latency-зависимость, отлично работает с русским (топ-3 на MTEB ru).

Модель: ``intfloat/multilingual-e5-small`` (384d, ~120 МБ ONNX-весов,
скачиваются при первом старте контейнера в ``/root/.cache/fastembed``).
Для прода кэш желательно смонтировать как volume — иначе rebuild = повторное
скачивание. E5 требует префиксов: ``passage: <text>`` для хранимого,
``query: <text>`` для запроса — выставляем автоматически в :func:`embed_text`
и :func:`embed_query`.

Инференс синхронный (onnxruntime CPU). Чтобы не блокировать event-loop,
все вызовы оборачиваем в ``loop.run_in_executor`` с дефолтным ThreadPool.
Это безопасно: онxruntime сам параллелит внутри и держит GIL отпущенным
во время matmul. Один проход на e5-small — 5-20 мс/текст на современном CPU.

Контракт:
* :func:`embed_text` / :func:`embed_query` — для хранения / поиска
  (различаются только префиксом).
* :func:`save_embedding`, :func:`save_embeddings_bulk` — параметризованный
  UPDATE с pgvector-литералом.
* :func:`backfill_missing` — фоновый джоб: добивает партию ai_memories без
  embedding (см. :func:`setup_embeddings_backfill`).
* :func:`_vector_literal` — публичная по факту (используется в memory.py для
  построения SQL ранкера); см. там же.

Дёшево: ~0 ₽/мес, 192 факта backfill ≈ 5 сек на CPU. LRU-кэш в памяти
процесса гасит повторы внутри сессии.

Никогда не валит бот: при сбое загрузки модели — embedder no-op, retrieval
тихо деградирует к BM25+trigram.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.models import AiMemory, AiSetting

logger = get_logger(__name__)

# Настройки кэшируем на 60с: меняются вручную из админки, мгновенный pickup
# не нужен — джоб всё равно работает раз в несколько минут.
_CONFIG_TTL_SEC = 60.0
# Batch для backfill. Локальный инференс — потолок чисто CPU; 64 строки
# отрабатывают ~0.5-2 сек, что приемлемо в фоновом джобе.
_BACKFILL_BATCH = 64
# Кэш embeddings внутри процесса (наивный LRU поверх dict). Те же запросы
# часто повторяются (один пользователь — несколько реплик). 1024 × 384 float
# ≈ 1.5 МБ — пренебрежимо.
_CACHE_LIMIT = 1024

# Маркер локального провайдера в ``ai_settings.embedding_base_url``.
# Историческое поле base_url осталось от HTTP-эпохи; теперь играет роль
# переключателя «локально vs внешний HTTP». Сейчас поддерживаем только
# локальный путь — других провайдеров нет; HTTP-вариант снят, потому что
# wellflow embeddings не отдаёт, а второй платёжный канал заводить не стали.
_LOCAL_MARKER = "local:fastembed"


@dataclass(slots=True, frozen=True)
class EmbeddingConfig:
    """Снимок настроек embedder. Получают через get_embedding_config()."""

    base_url: str
    model: str
    dim: int

    @property
    def usable(self) -> bool:
        """True, если можно реально звать провайдер.

        Для локального провайдера требуем явный маркер ``local:fastembed`` —
        чтобы пустой/случайный base_url не запускал тяжёлую загрузку модели.
        """
        return self.base_url == _LOCAL_MARKER and bool(self.model)


# Per-process кэш конфига (одна запись).
_cfg_cache: tuple[float, EmbeddingConfig] | None = None
_cfg_lock = asyncio.Lock()

# Singleton модели fastembed. Загружаем лениво — ~3-5 сек на старте, плюс
# скачивание весов при первом запуске. После загрузки переиспользуется.
_model: Any = None
_model_lock = asyncio.Lock()


async def get_embedding_config(session: AsyncSession) -> EmbeddingConfig:
    """Лениво подтягивает настройки embedder из ``ai_settings`` с TTL-кэшем."""
    global _cfg_cache
    now = time.monotonic()
    if _cfg_cache and (now - _cfg_cache[0]) < _CONFIG_TTL_SEC:
        return _cfg_cache[1]
    async with _cfg_lock:
        # double-check после lock — параллельные корутины не дублируют запрос.
        if _cfg_cache and (time.monotonic() - _cfg_cache[0]) < _CONFIG_TTL_SEC:
            return _cfg_cache[1]
        rows = (
            await session.execute(
                select(AiSetting.key, AiSetting.value).where(
                    AiSetting.key.in_(
                        (
                            "embedding_base_url",
                            "embedding_model",
                            "embedding_dim",
                        )
                    )
                )
            )
        ).all()
        kv = {k: v for k, v in rows}
        cfg = EmbeddingConfig(
            base_url=str(kv.get("embedding_base_url") or ""),
            model=str(kv.get("embedding_model") or ""),
            # dim хранится как JSONB-число; приведение через int() безопасно.
            dim=int(kv.get("embedding_dim") or 384),
        )
        _cfg_cache = (time.monotonic(), cfg)
        return cfg


def invalidate_config_cache() -> None:
    """Сбросить кэш конфига — звать из админки после смены настроек."""
    global _cfg_cache
    _cfg_cache = None


# --- Загрузка модели --------------------------------------------------------


def _load_model_sync(model_name: str) -> Any:
    """Синхронная загрузка fastembed. Вызывается из executor.

    Импорт внутри: тяжёлый (onnxruntime), не нужен при выключенном embedder.
    Падение импорта/загрузки — возвращаем None: вызывающий получает no-op
    и не валит бот.
    """
    try:
        from fastembed import TextEmbedding  # type: ignore
    except Exception:  # noqa: BLE001
        logger.warning("fastembed import failed (no embeddings)", exc_info=True)
        return None
    try:
        # cache_dir по умолчанию ~/.cache/fastembed — в Docker это
        # /root/.cache/fastembed. Для прода желательно volume.
        return TextEmbedding(model_name=model_name)
    except Exception:  # noqa: BLE001
        logger.warning(
            "fastembed model load failed: %s", model_name, exc_info=True
        )
        return None


async def _get_model(cfg: EmbeddingConfig) -> Any:
    """Lazy singleton модели. None — embedder выключен или сломан."""
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        loop = asyncio.get_running_loop()
        model = await loop.run_in_executor(None, _load_model_sync, cfg.model)
        _model = model
        if model is not None:
            logger.info("fastembed loaded: %s (dim=%d)", cfg.model, cfg.dim)
        return model


# --- Кэш LRU для одиночных запросов -----------------------------------------

# Простой dict-кэш с FIFO-вытеснением. Ключ — (model, prefix, text_normalized).
# Префикс важен: e5 даёт разные вектора для "query: X" и "passage: X".
_cache_dict: dict[tuple[str, str, str], tuple[float, ...]] = {}


def _normalize(text_in: str) -> str:
    """Нормализуем для кэш-ключа: trim + collapse whitespace + lower."""
    return " ".join((text_in or "").lower().split())[:4000]


def _cache_get(key: tuple[str, str, str]) -> list[float] | None:
    v = _cache_dict.get(key)
    return list(v) if v is not None else None


def _cache_put(key: tuple[str, str, str], vec: list[float]) -> None:
    if len(_cache_dict) >= _CACHE_LIMIT:
        # FIFO: дропаем самый старый (Python 3.7+ dict сохраняет порядок).
        try:
            oldest = next(iter(_cache_dict))
            _cache_dict.pop(oldest, None)
        except StopIteration:
            pass
    _cache_dict[key] = tuple(vec)


# --- Инференс ---------------------------------------------------------------


def _embed_sync(model: Any, texts: list[str]) -> list[list[float]] | None:
    """Синхронный батч-инференс. None при ошибке.

    fastembed.embed() возвращает generator np.ndarray; материализуем сразу.
    """
    try:
        out: list[list[float]] = []
        for vec in model.embed(texts):
            out.append([float(x) for x in vec])
        return out
    except Exception:  # noqa: BLE001
        logger.warning("fastembed inference failed", exc_info=True)
        return None


async def _embed_batch(
    cfg: EmbeddingConfig, prefix: str, texts: list[str]
) -> list[list[float]] | None:
    """Embed пачкой с e5-префиксом. None при выключенном embedder/ошибке."""
    if not cfg.usable:
        return None
    model = await _get_model(cfg)
    if model is None:
        return None
    # e5 требует префиксы: passage: для хранимых, query: для поисковых.
    prefixed = [f"{prefix}: {t}" for t in texts]
    loop = asyncio.get_running_loop()
    vecs = await loop.run_in_executor(None, _embed_sync, model, prefixed)
    if vecs is None:
        return None
    # Жёсткая проверка размерности: смена модели без миграции колонки приведёт
    # к INSERT с неверной длиной и ошибке pgvector. Отсекаем некорректные.
    good: list[list[float]] = []
    for v in vecs:
        if len(v) != cfg.dim:
            logger.warning(
                "embedding dim mismatch: got=%d cfg=%d (model=%s)",
                len(v), cfg.dim, cfg.model,
            )
            return None
        good.append(v)
    return good


# --- Публичный API ----------------------------------------------------------


async def embed_text(session: AsyncSession, text_in: str) -> list[float] | None:
    """Embed одного факта для ХРАНЕНИЯ (passage-режим e5).

    None при выключенном embedder или ошибке. Никогда не бросает.
    """
    s = (text_in or "").strip()
    if not s:
        return None
    cfg = await get_embedding_config(session)
    if not cfg.usable:
        return None
    key = (cfg.model, "p", _normalize(s))
    cached = _cache_get(key)
    if cached is not None:
        return cached
    vecs = await _embed_batch(cfg, "passage", [s])
    if not vecs:
        return None
    vec = vecs[0]
    _cache_put(key, vec)
    return vec


async def embed_query(session: AsyncSession, text_in: str) -> list[float] | None:
    """Embed запроса для ПОИСКА (query-режим e5).

    Отдельно от embed_text, потому что e5 без правильного префикса режет
    качество поиска ~на 5-10% по MTEB.
    """
    s = (text_in or "").strip()
    if not s:
        return None
    cfg = await get_embedding_config(session)
    if not cfg.usable:
        return None
    key = (cfg.model, "q", _normalize(s))
    cached = _cache_get(key)
    if cached is not None:
        return cached
    vecs = await _embed_batch(cfg, "query", [s])
    if not vecs:
        return None
    vec = vecs[0]
    _cache_put(key, vec)
    return vec


def _vector_literal(vec: list[float]) -> str:
    """pgvector принимает строковый литерал ``'[0.1,0.2,...]'`` — параметром.

    Float-формат без лишних пробелов; точность float сохраняется repr-ом.
    Публичная для memory.py (строит SQL ранкер с этим литералом).
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def save_embedding(
    session: AsyncSession, memory_id: int, vec: list[float]
) -> None:
    """UPDATE ai_memories.embedding для одной строки. Commit на вызывающем."""
    await session.execute(
        text(
            "UPDATE ai_memories SET embedding = CAST(:v AS vector) "
            "WHERE id = :id"
        ),
        {"v": _vector_literal(vec), "id": memory_id},
    )


async def save_embeddings_bulk(
    session: AsyncSession, pairs: list[tuple[int, list[float]]]
) -> None:
    """Bulk-апдейт пачкой. Один SQL-RTT вместо N — критично для backfill.

    Использует ``UPDATE ... FROM (VALUES ...)`` с явным CAST в vector;
    параметры именованные, инъекции невозможны.

    INCIDENT 2026-06-18: без CAST(:id AS bigint) asyncpg/драйвер биндил
    параметр как text (внутри VALUES контекста нет колонки-цели для
    инференса типа), и `WHERE m.id = v.id` падал с
    ``operator does not exist: bigint = text``. Явный CAST обоих
    параметров фиксирует типы строк VALUES однозначно — id как bigint,
    emb как vector — и пайплайн backfill снова доезжает до commit.
    """
    if not pairs:
        return
    parts: list[str] = []
    params: dict[str, Any] = {}
    for i, (mid, vec) in enumerate(pairs):
        parts.append(f"(CAST(:id{i} AS bigint), CAST(:v{i} AS vector))")
        params[f"id{i}"] = int(mid)
        params[f"v{i}"] = _vector_literal(vec)
    sql = (
        "UPDATE ai_memories AS m SET embedding = v.emb "
        "FROM (VALUES " + ", ".join(parts) + ") AS v(id, emb) "
        "WHERE m.id = v.id"
    )
    await session.execute(text(sql), params)


# --- Backfill ---------------------------------------------------------------


async def backfill_missing(session: AsyncSession) -> int:
    """Одна итерация бэкафилла: добивает партию памятей без embedding.

    Возвращает число обработанных. 0 — либо нечего делать, либо embedder
    выключен. Безопасно вызывать часто: лимит партии ограничивает стоимость
    одного тика (CPU-инференс ~0.5-2 сек на 64 строки).
    """
    cfg = await get_embedding_config(session)
    if not cfg.usable:
        return 0
    rows = (
        await session.execute(
            select(AiMemory.id, AiMemory.fact)
            .where(AiMemory.embedding.is_(None))
            .order_by(AiMemory.weight.desc(), AiMemory.id.desc())
            .limit(_BACKFILL_BATCH)
        )
    ).all()
    if not rows:
        return 0
    vecs = await _embed_batch(cfg, "passage", [r.fact for r in rows])
    if not vecs:
        return 0
    pairs = [(int(r.id), v) for r, v in zip(rows, vecs, strict=True)]
    await save_embeddings_bulk(session, pairs)
    await session.commit()
    logger.info("embeddings backfill: %d rows", len(pairs))
    return len(pairs)


def setup_embeddings_backfill(
    scheduler,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    minutes: int = 5,
) -> None:
    """Зарегистрировать фоновую джобу backfill (по умолчанию каждые 5 минут).

    Идемпотентно: ``replace_existing=True`` гарантирует один экземпляр после
    рестарта. Любой сбой — молча в лог, бот продолжает жить.
    """

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                await backfill_missing(session)
        except Exception:  # noqa: BLE001
            logger.warning("embeddings backfill job failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_embeddings_backfill",
        replace_existing=True,
    )
