"""BM25 + trigram retrieval для ai_memories (умная память друна).

Раньше отбор фактов в `memory.scored_memories` тянул из БД пул кандидатов по
весу/свежести и ранжировал в Python через грубое пересечение токенов с запросом
(`memory._tokenize`). Это давало смещение «тяжёлое побеждает свежее» и не
понимало морфологии — «слил/слива/сливает» считались разными словами, а
память по теме часто не всплывала вообще.

Здесь поднимаем поиск на уровень Postgres: full-text search с русской
морфологией (tsvector + GIN) и pg_trgm для устойчивости к опечаткам и
коротким запросам. Embeddings через текущий gateway недоступны
(`/v1/embeddings` → 501), поэтому BM25/FTS — самый сильный доступный сейчас
ретривал; при появлении embedding-endpoint поверх легко добавить vector-колонку.

Идемпотентно. Расширение `pg_trgm` создаём только если у роли есть права;
иначе оставляем без trigram-индекса (FTS всё равно работает).

Revision ID: 0049_ai_memory_bm25
Revises: 0048_ai_messages_role_idx
Create Date: 2026-06-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0049_ai_memory_bm25"
down_revision: Union[str, None] = "0048_ai_messages_role_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Расширения. CREATE EXTENSION IF NOT EXISTS требует superuser/createdb прав;
    # оборачиваем в DO-блок, чтобы миграция не падала на ограниченной роли.
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                CREATE EXTENSION IF NOT EXISTS pg_trgm;
            EXCEPTION WHEN insufficient_privilege THEN
                RAISE NOTICE 'pg_trgm extension not available; trigram index skipped';
            END;
        END$$;
        """
    )

    # FTS-колонка как GENERATED ALWAYS (всегда консистентна с fact, без триггеров).
    # Конфиг 'russian' включён в Postgres по умолчанию; пытаемся его, иначе
    # деградируем к 'simple' (без морфологии, но всё равно индексируем).
    op.execute(
        """
        DO $$
        DECLARE
            has_russian boolean;
        BEGIN
            SELECT EXISTS(
                SELECT 1 FROM pg_ts_config WHERE cfgname = 'russian'
            ) INTO has_russian;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'ai_memories' AND column_name = 'fact_tsv'
            ) THEN
                IF has_russian THEN
                    EXECUTE 'ALTER TABLE ai_memories ADD COLUMN fact_tsv tsvector '
                            'GENERATED ALWAYS AS (to_tsvector(''russian'', coalesce(fact, ''''))) STORED';
                ELSE
                    EXECUTE 'ALTER TABLE ai_memories ADD COLUMN fact_tsv tsvector '
                            'GENERATED ALWAYS AS (to_tsvector(''simple'', coalesce(fact, ''''))) STORED';
                END IF;
            END IF;
        END$$;
        """
    )

    # GIN-индекс по tsvector — основа FTS-ранжирования (ts_rank).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_memories_fact_tsv "
        "ON ai_memories USING GIN (fact_tsv)"
    )

    # Trigram-индекс по сырому тексту — устойчив к опечаткам/коротким словам,
    # дополняет FTS (similarity()). Только если pg_trgm доступен.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
                EXECUTE 'CREATE INDEX IF NOT EXISTS ix_ai_memories_fact_trgm '
                        'ON ai_memories USING GIN (fact gin_trgm_ops)';
            END IF;
        END$$;
        """
    )

    # Покрывающий индекс для частого фильтра «не протухшее + по типу».
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_memories_kind_expires "
        "ON ai_memories (kind, expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ai_memories_kind_expires")
    op.execute("DROP INDEX IF EXISTS ix_ai_memories_fact_trgm")
    op.execute("DROP INDEX IF EXISTS ix_ai_memories_fact_tsv")
    op.execute(
        "ALTER TABLE ai_memories DROP COLUMN IF EXISTS fact_tsv"
    )
    # pg_trgm не дропаем — может использоваться другими таблицами.
