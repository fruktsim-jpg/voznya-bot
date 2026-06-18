"""pgvector + embedding для долгой памяти друна.

Замена дешёвого token-overlap скоринга (memory.py:380-409) на честный
семантический поиск. Гибридная схема: кандидаты отбираются по cosine-расстоянию
к запросу, далее тот же ранкер (вес + recency + сходство) выбирает топ.

Поднимает:
* extension ``vector`` (pgvector). Поэтому образ db в docker-compose должен быть
  ``pgvector/pgvector:pg16`` (а не голый ``postgres:16-alpine``); volume не
  трогается — это тот же мажор PG 16.
* колонку ``ai_memories.embedding`` (vector(1536)) — размерность под
  ``text-embedding-3-small`` (OpenAI). Если решим сменить провайдера на 512d
  (voyage-3-lite) или 1024d — нужна отдельная миграция с пересборкой колонки.
* HNSW-индекс по cosine. HNSW даёт O(log n) поиск даже на сотнях тысяч строк,
  без перестройки при INSERT; m=16/ef_construction=64 — дефолт pgvector,
  отлично работает для нашего объёма.
* seed-ключи ``embedding_*`` в ``ai_settings`` — пустые, заполняются админом
  (или вручную в БД). Пока ключа нет — embedder no-op, поиск тихо
  деградирует к старому token-overlap (см. ``drun/memory.scored_memories``).

Идемпотентно: всё через ``IF NOT EXISTS``. Можно безопасно прогонять повторно.
Downgrade оставляет данные ai_memories, удаляет лишь колонку и индекс.

Revision ID: 0050_ai_memory_embeddings
Revises: 0049_ai_memory_bm25
Create Date: 2026-06-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0050_ai_memory_embeddings"
down_revision: Union[str, None] = "0049_ai_memory_bm25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    statements = [
        # pgvector. Если extension недоступен (старый образ postgres:16-alpine
        # без contrib) — миграция упадёт здесь с понятной ошибкой; это сигнал
        # к смене образа на pgvector/pgvector:pg16. Без extension векторный
        # столбец создать нельзя.
        "CREATE EXTENSION IF NOT EXISTS vector",

        # Колонка embedding. 1536 = размерность text-embedding-3-small (OpenAI).
        # NULL разрешён: старые записи без embedding обрабатываются фоновым
        # backfill-джобом (drun/embeddings.py); поиск умеет работать вперемешку.
        "ALTER TABLE ai_memories ADD COLUMN IF NOT EXISTS embedding vector(1536)",

        # HNSW-индекс по cosine. Это рабочий индекс для retrieval; ivfflat
        # хуже на маленьких корпусах и требует пересборки. cosine — то, что
        # семантически ожидается от text-3-small (нормированные вектора).
        # WHERE embedding IS NOT NULL — частичный индекс, чтобы не тратить
        # место на ещё не-embedded строки.
        """
        CREATE INDEX IF NOT EXISTS ix_ai_memories_embedding_hnsw
        ON ai_memories USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """,

        # Seed-настройки embedding-провайдера. Пустые значения = no-op:
        # embedder ничего не делает, retrieval падает на token-overlap.
        # Когда админ заполнит api_key — фоновый backfill начнёт заполнять
        # старые строки, а новые памяти будут embedded сразу при создании.
        # ON CONFLICT DO NOTHING — не затираем уже выставленные продом ключи.
        """
        INSERT INTO ai_settings (key, value)
        VALUES ('embedding_base_url', '"https://api.openai.com/v1"'::jsonb)
        ON CONFLICT (key) DO NOTHING
        """,
        """
        INSERT INTO ai_settings (key, value)
        VALUES ('embedding_api_key', '""'::jsonb)
        ON CONFLICT (key) DO NOTHING
        """,
        """
        INSERT INTO ai_settings (key, value)
        VALUES ('embedding_model', '"text-embedding-3-small"'::jsonb)
        ON CONFLICT (key) DO NOTHING
        """,
        # Размерность хранится отдельно — на случай смены провайдера; должна
        # совпадать с типом колонки. Меняя — нужна новая миграция.
        """
        INSERT INTO ai_settings (key, value)
        VALUES ('embedding_dim', '1536'::jsonb)
        ON CONFLICT (key) DO NOTHING
        """,
    ]
    for stmt in statements:
        op.execute(stmt)


def downgrade() -> None:
    # Embedding-данные — побочный продукт; сами факты остаются. Extension
    # vector не дропаем: возможно, использует кто-то ещё в БД.
    op.execute("DROP INDEX IF EXISTS ix_ai_memories_embedding_hnsw")
    op.execute("ALTER TABLE ai_memories DROP COLUMN IF EXISTS embedding")
    op.execute("DELETE FROM ai_settings WHERE key IN ("
               "'embedding_base_url','embedding_api_key',"
               "'embedding_model','embedding_dim')")
