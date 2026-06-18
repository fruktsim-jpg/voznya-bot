"""fastembed: vector(384) + локальный провайдер для долгой памяти друна.

Зачем: wellflow gateway отдаёт 501 на /v1/embeddings для всех моделей
(проверено 18.06.2026), второй платёжный канал OpenAI заводить не стали.
Переключаемся на self-hosted fastembed с моделью intfloat/multilingual-e5-small
(384d, ~120 МБ ONNX). Перебиваем артефакты миграции 0050 (1536d под OpenAI)
на 384d и переключаем seed-настройки на маркер ``local:fastembed``.

Изменения:
* пересоздаём ``ai_memories.embedding`` как ``vector(384)``. На момент 0050
  колонка пуста (нечем было embed-ить), поэтому DROP без потери данных —
  если в проде уже успели заполнить, миграция увидит ненулевые embedding
  и упадёт с понятной ошибкой (см. защитный SELECT ниже);
* пересоздаём HNSW под новую колонку (старый удаляется вместе с колонкой);
* обновляем seed-настройки: ``embedding_base_url='local:fastembed'`` (это
  маркер локального провайдера в drun/embeddings.py), модель и dim под e5.

Идемпотентно. Downgrade возвращает колонку на vector(1536) и старые seed'ы.

Revision ID: 0051_ai_memory_fastembed_384
Revises: 0050_ai_memory_embeddings
Create Date: 2026-06-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0051_ai_memory_fastembed_384"
down_revision: Union[str, None] = "0050_ai_memory_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Защита: если кто-то уже успел залить embedding на старой размерности,
    # тихий DROP COLUMN потеряет данные. Падаем явно — оператор должен
    # сначала truncate-нуть embedding и повторить миграцию.
    op.execute(
        """
        DO $$
        DECLARE n bigint;
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'ai_memories' AND column_name = 'embedding'
          ) THEN
            EXECUTE 'SELECT count(*) FROM ai_memories WHERE embedding IS NOT NULL' INTO n;
            IF n > 0 THEN
              RAISE EXCEPTION 'ai_memories.embedding has % non-null rows; '
                'TRUNCATE the column manually before switching to vector(384)', n;
            END IF;
          END IF;
        END$$;
        """
    )

    # Индекс зависит от колонки — дроп первым.
    op.execute("DROP INDEX IF EXISTS ix_ai_memories_embedding_hnsw")
    op.execute("ALTER TABLE ai_memories DROP COLUMN IF EXISTS embedding")

    # Новая колонка под multilingual-e5-small.
    op.execute("ALTER TABLE ai_memories ADD COLUMN embedding vector(384)")

    # HNSW по cosine — тот же выбор операций, что и в 0050.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ai_memories_embedding_hnsw
        ON ai_memories USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )

    # Переключение seed-настроек на локальный провайдер. Затираем безусловно:
    # это смена эпохи embedder, старые значения (api.openai.com / 1536) уже
    # не валидны под колонку vector(384).
    op.execute(
        """
        UPDATE ai_settings SET value = '"local:fastembed"'::jsonb
         WHERE key = 'embedding_base_url'
        """
    )
    op.execute(
        """
        UPDATE ai_settings SET value = '"intfloat/multilingual-e5-small"'::jsonb
         WHERE key = 'embedding_model'
        """
    )
    op.execute(
        """
        UPDATE ai_settings SET value = '384'::jsonb
         WHERE key = 'embedding_dim'
        """
    )
    # api_key больше не нужен (локальный инференс), но ключ оставляем —
    # вдруг вернёмся к HTTP-провайдеру. Просто чистим значение.
    op.execute(
        """
        UPDATE ai_settings SET value = '""'::jsonb
         WHERE key = 'embedding_api_key'
        """
    )


def downgrade() -> None:
    # Возвращаемся к схеме 0050: vector(1536) + OpenAI seed'ы.
    op.execute("DROP INDEX IF EXISTS ix_ai_memories_embedding_hnsw")
    op.execute("ALTER TABLE ai_memories DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE ai_memories ADD COLUMN embedding vector(1536)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ai_memories_embedding_hnsw
        ON ai_memories USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )
    op.execute(
        "UPDATE ai_settings SET value = '\"https://api.openai.com/v1\"'::jsonb "
        "WHERE key = 'embedding_base_url'"
    )
    op.execute(
        "UPDATE ai_settings SET value = '\"text-embedding-3-small\"'::jsonb "
        "WHERE key = 'embedding_model'"
    )
    op.execute(
        "UPDATE ai_settings SET value = '1536'::jsonb WHERE key = 'embedding_dim'"
    )
