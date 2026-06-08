"""Полный вайп экономики перед стартом Сезона 1 (APPROVED владельцем).

Обнуляет всё игровое, СОХРАНЯЯ идентичность и сообщения. См.
docs/SEASON_1_WIPE_AND_DESIGN.md.

🔴 Стирается: балансы/заработок/MMR/счётчики на users; журналы transactions,
   mmr_entries, case_openings, inventory(+history), gift_transactions,
   user_achievements, purchase_history, stars_ledger, audit_log; резервы
   gift_catalog; сезонные таблицы. Архивирование НЕ делаем (владелец разрешил
   удалять полностью — приоритет простоты).

🟢 Не трогается: users (строки), username/first_name/photo, messages_count,
   admin_roles, app_settings, каталоги (case_definitions/case_rewards/
   gift_catalog позиции), вся техническая инфраструктура.

ВАЖНО: вайп — РАЗОВАЯ операция перед Сезоном 1. ``downgrade`` восстановить
данные не может (они удалены безвозвратно) — это no-op с пояснением.

Запуск: ``alembic upgrade 0034_season_1_wipe`` (после 0033).

Revision ID: 0034_season_1_wipe
Revises: 0033_season_system
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0034_season_1_wipe"
down_revision: Union[str, None] = "0033_season_system"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Журналы/таблицы, которые чистим целиком. Чистим через DELETE (не TRUNCATE),
# чтобы не упасть на отсутствующих таблицах на разнящихся окружениях — каждую
# в отдельном best-effort блоке.
_WIPE_TABLES = [
    "transactions",
    "mmr_entries",
    "case_openings",
    "inventory_history",
    "inventory_instances",
    "inventory",
    "gift_transactions",
    "purchase_history",
    "stars_ledger",
    "user_achievements",
    "audit_log",
    # Сезонные журналы (на случай повторного прогона/тестовых данных).
    "season_mmr_entries",
    "season_titles",
    "daily_claims",
    "weekly_mission_progress",
    "login_streaks",
    "seasons",
]


def _safe(conn, sql: str) -> None:
    """Выполняет SQL, не падая на отсутствующей таблице/колонке."""
    try:
        conn.exec_driver_sql(sql)
    except Exception:  # noqa: BLE001 — окружения отличаются, вайп best-effort
        pass


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Чистим игровые журналы и сезонные таблицы.
    for table in _WIPE_TABLES:
        _safe(conn, f"DELETE FROM {table}")

    # 2. Обнуляем игровые поля пользователей (идентичность и сообщения целы).
    _safe(
        conn,
        """
        UPDATE users SET
            balance = 0,
            total_earned = 0,
            total_spent = 0,
            farm_streak = 0,
            max_farm_streak = 0,
            last_farm_at = NULL,
            treasures_found = 0,
            duels_won = 0,
            duels_lost = 0,
            pidor_count = 0,
            farm_success_count = 0,
            casino_games_count = 0,
            casino_loss_streak = 0,
            duel_loss_streak = 0,
            max_casino_loss = 0,
            mmr = 0,
            season_mmr = 0
        """,
    )

    # 3. Сбрасываем счётчики каталога подарков (сами позиции остаются).
    _safe(conn, "UPDATE gift_catalog SET reserved = 0, sold_count = 0")


def downgrade() -> None:
    # Вайп необратим: удалённые игровые данные не восстановить. No-op.
    pass
