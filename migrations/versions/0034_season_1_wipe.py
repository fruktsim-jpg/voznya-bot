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

🛡️ ЗАЩИТА ОТ СЛУЧАЙНОГО ВАЙПА (P0): Dockerfile запускает ``alembic upgrade head``
при КАЖДОМ деплое. Если развернуть бота на новой/восстановленной из старого
бэкапа БД, которая ещё не прошла 0034, авто-upgrade молча сотрёт всю экономику.
Поэтому деструктивная часть выполняется ТОЛЬКО при явном
``ALLOW_SEASON_1_WIPE=true`` в окружении. Без флага миграция помечается как
применённая, но НИЧЕГО не удаляет (громкий warning в лог). Прод уже прошёл 0034
(Alembic его не перезапустит), новая БД пуста — поэтому флаг нужен лишь в тот
единственный момент, когда вайп действительно намеренный.

Запуск (намеренный вайп): ``ALLOW_SEASON_1_WIPE=true alembic upgrade 0034_season_1_wipe``.

Revision ID: 0034_season_1_wipe
Revises: 0033_season_system
Create Date: 2026-06-08
"""
import logging
import os
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


def _wipe_allowed() -> bool:
    """Деструктивный вайп разрешён только явным ALLOW_SEASON_1_WIPE=true."""
    return os.environ.get("ALLOW_SEASON_1_WIPE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def upgrade() -> None:
    log = logging.getLogger("alembic.runtime.migration")

    # P0-защита: без явного флага НЕ удаляем ничего. Миграция всё равно
    # помечается применённой (цепочка ревизий не ломается), но данные целы —
    # это спасает новые/восстановленные БД от молчаливого авто-вайпа при деплое.
    if not _wipe_allowed():
        log.warning(
            "0034_season_1_wipe: ПРОПУЩЕН (ALLOW_SEASON_1_WIPE не задан). "
            "Деструктивный вайп экономики НЕ выполнен — данные сохранены. "
            "Для намеренного вайпа: ALLOW_SEASON_1_WIPE=true alembic upgrade head."
        )
        return

    log.warning(
        "0034_season_1_wipe: ALLOW_SEASON_1_WIPE задан — выполняю НЕОБРАТИМЫЙ "
        "вайп экономики (балансы, MMR, инвентарь, журналы, gift_transactions)."
    )
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
