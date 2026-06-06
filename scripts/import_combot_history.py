#!/usr/bin/env python3
"""Одноразовый импорт исторической статистики Combot в собственную БД.

Тянет два рабочих endpoint'а Combot API v5 и раскладывает их по изолированным
``combot_*`` таблицам (см. миграцию ``0012_combot_import_foundation``):

    channel_users     → combot_user_stats        (пер-юзер: messages/xp/rep/...)
    channel_analytics → combot_daily_stats        (дневные агрегаты)
                        + combot_activity_heatmap  (heat-map 24×7)

Каждый запуск пишет строку в ``combot_import_runs`` (running → success/failed)
и в конце печатает статистику + сверку Combot ``user_id`` с нашей ``users``.

ГРАНИЦЫ (соблюдаются жёстко):
  * НЕ трогает users, баланс/transactions, inventory*, shop_*, gift_transactions;
  * НЕ начисляет достижения;
  * НЕ создаёт UI;
  * НЕ применяет миграцию (её надо накатить заранее: alembic upgrade head);
  * НЕ запускается автоматически — только вручную.

Идемпотентность: все три набора пишутся через PostgreSQL ``INSERT ... ON
CONFLICT DO UPDATE`` по натуральным ключам (user_id / day / hour+weekday),
поэтому повторный запуск обновляет снимок, а не плодит дубликаты.

Конфигурация (через окружение, ключ — секрет, в git не коммитим):
    COMBOT_API_KEY   — обязательный API-ключ Combot;
    COMBOT_CHAT_ID   — chat_id чата (по умолчанию берётся CHAT_ID из настроек);
    COMBOT_FROM_MS   — начало периода в Unix ms (по умолчанию 2020-01-01);
    DATABASE_URL     — строка подключения (как у бота).

Запуск (см. README в конце задачи):
    python -m scripts.import_combot_history --dry-run
    python -m scripts.import_combot_history
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Гарантируем, что корень проекта в sys.path при запуске как файла.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.models import (  # noqa: E402
    CombotActivityHeatmap,
    CombotDailyStats,
    CombotImportRun,
    CombotUserStats,
    User,
)

COMBOT_BASE_URL = "https://api.combot.org/v5/"
DEFAULT_FROM_MS = 1577836800000  # 2020-01-01 00:00:00 UTC
USERS_PAGE_LIMIT = 500  # хватает на весь ростер за один запрос (см. COMBOT_MIGRATION.md)
HTTP_TIMEOUT_SEC = 60


# --------------------------------------------------------------------------- #
# HTTP-слой (stdlib, без новых зависимостей)
# --------------------------------------------------------------------------- #
class CombotApiError(RuntimeError):
    """Ошибка обращения к Combot API."""


def _redact(url: str) -> str:
    """Прячет api_key в URL перед логированием/исключением."""
    return urllib.parse.urlsplit(url)._replace(query="api_key=***").geturl()


def _combot_get(endpoint: str, params: dict[str, Any], api_key: str) -> dict[str, Any]:
    """GET к Combot, разбор конверта ``{"ok":true,"result":...}``."""
    query = urllib.parse.urlencode({**params, "api_key": api_key})
    url = f"{COMBOT_BASE_URL}{endpoint}?{query}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        raise CombotApiError(
            f"HTTP {exc.code} от {endpoint} ({_redact(url)})"
        ) from exc
    except urllib.error.URLError as exc:
        raise CombotApiError(f"Сеть недоступна для {endpoint}: {exc.reason}") from exc

    if not payload.get("ok"):
        raise CombotApiError(
            f"{endpoint} вернул ошибку: "
            f"{payload.get('error_code')} {payload.get('description')}"
        )
    return payload.get("result") or {}


def fetch_channel_users(
    chat_id: int, from_ms: int, to_ms: int, api_key: str
) -> list[dict[str, Any]]:
    """Тянет полный ростер участников (с пагинацией на всякий случай)."""
    users: list[dict[str, Any]] = []
    page = 0
    while True:
        result = _combot_get(
            "channel_users",
            {
                "chat_id": chat_id,
                "from": from_ms,
                "to": to_ms,
                "page": page,
                "limit": USERS_PAGE_LIMIT,
            },
            api_key,
        )
        batch = result.get("users") or result.get("items") or []
        # Combot кладёт записи прямо в result-список под разными ключами;
        # подстраховка — если result сам является списком.
        if not batch and isinstance(result, list):
            batch = result
        users.extend(batch)

        pagination = result.get("pagination") or {}
        if pagination.get("has_next") and batch:
            page += 1
            continue
        break
    return users


def fetch_channel_analytics(
    chat_id: int, from_ms: int, to_ms: int, api_key: str
) -> dict[str, Any]:
    """Тянет агрегированные тайм-серии и heat-map за весь период."""
    return _combot_get(
        "channel_analytics",
        {"chat_id": chat_id, "from": from_ms, "to": to_ms},
        api_key,
    )


# --------------------------------------------------------------------------- #
# Преобразования ответа API → строки таблиц
# --------------------------------------------------------------------------- #
def _ms_to_dt(value: Any) -> datetime | None:
    """Unix ms → tz-aware datetime (UTC). Пустые/нулевые → None."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _ms_to_date(value: Any) -> date | None:
    """Unix ms → дата (UTC)."""
    dt = _ms_to_dt(value)
    return dt.date() if dt else None


def _to_int(value: Any) -> int:
    """Безопасное приведение к int (None/пусто → 0)."""
    try:
        return int(value) if value is not None else 0
    except (ValueError, TypeError):
        return 0


def build_user_rows(
    records: list[dict[str, Any]], run_id: int | None
) -> list[dict[str, Any]]:
    """Запись Combot → строка combot_user_stats."""
    rows: list[dict[str, Any]] = []
    for rec in records:
        user_id = rec.get("user_id")
        if user_id is None:
            continue
        u_list = rec.get("u") or []
        first = u_list[0] if u_list else {}
        rows.append(
            {
                "user_id": int(user_id),
                "username": (first.get("username") or None),
                "title": (first.get("title") or None),
                "joined_at": _ms_to_dt(rec.get("joined")),
                "days_since_joined": (
                    _to_int(rec.get("dsj")) if rec.get("dsj") is not None else None
                ),
                "messages": _to_int(rec.get("messages")),
                "xp": _to_int(rec.get("xp")),
                "rep": _to_int(rec.get("rep")),
                "last_message_at": _ms_to_dt(rec.get("last_message")),
                "import_run_id": run_id,
                "raw": rec,
            }
        )
    return rows


def build_daily_rows(
    analytics: dict[str, Any], run_id: int | None
) -> list[dict[str, Any]]:
    """Тайм-серии channel_analytics → строки combot_daily_stats (по дню)."""
    buckets: dict[date, dict[str, int]] = defaultdict(
        lambda: {"messages": 0, "active_users": 0, "joins": 0, "leaves": 0}
    )
    series_map = {
        "messages": "messages",
        "active_users": "active_users",
        "joined": "joins",
        "left": "leaves",
    }
    for api_key, field in series_map.items():
        for pair in analytics.get(api_key) or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            day = _ms_to_date(pair[0])
            if day is None:
                continue
            buckets[day][field] = _to_int(pair[1])

    return [
        {"day": day, **vals, "import_run_id": run_id}
        for day, vals in sorted(buckets.items())
    ]


def build_heatmap_rows(
    analytics: dict[str, Any], run_id: int | None
) -> list[dict[str, Any]]:
    """analytics.hours → строки combot_activity_heatmap."""
    rows: list[dict[str, Any]] = []
    for triple in analytics.get("hours") or []:
        if not isinstance(triple, (list, tuple)) or len(triple) < 3:
            continue
        hour, weekday, count = _to_int(triple[0]), _to_int(triple[1]), _to_int(triple[2])
        if not (0 <= hour <= 23 and 0 <= weekday <= 6):
            continue
        rows.append(
            {
                "hour": hour,
                "weekday": weekday,
                "messages": count,
                "import_run_id": run_id,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Идемпотентные upsert'ы (PostgreSQL ON CONFLICT)
# --------------------------------------------------------------------------- #
async def _upsert(session, model, rows, index_elements, update_cols):
    """Батчевый INSERT ... ON CONFLICT DO UPDATE. Возвращает число строк."""
    if not rows:
        return 0
    stmt = pg_insert(model).values(rows)
    set_ = {col: getattr(stmt.excluded, col) for col in update_cols}
    set_["imported_at"] = func.now()
    stmt = stmt.on_conflict_do_update(index_elements=index_elements, set_=set_)
    await session.execute(stmt)
    return len(rows)


async def upsert_users(session, rows):
    return await _upsert(
        session,
        CombotUserStats,
        rows,
        ["user_id"],
        [
            "username",
            "title",
            "joined_at",
            "days_since_joined",
            "messages",
            "xp",
            "rep",
            "last_message_at",
            "import_run_id",
            "raw",
        ],
    )


async def upsert_daily(session, rows):
    return await _upsert(
        session,
        CombotDailyStats,
        rows,
        ["day"],
        ["messages", "active_users", "joins", "leaves", "import_run_id"],
    )


async def upsert_heatmap(session, rows):
    return await _upsert(
        session,
        CombotActivityHeatmap,
        rows,
        ["hour", "weekday"],
        ["messages", "import_run_id"],
    )


# --------------------------------------------------------------------------- #
# Сверка Combot user_id ↔ users.user_id
# --------------------------------------------------------------------------- #
async def match_users(session, user_rows) -> dict[str, Any]:
    """Сравнивает Combot user_id с нашей таблицей users. Возвращает отчёт."""
    combot_ids = [r["user_id"] for r in user_rows]
    if not combot_ids:
        return {"combot_total": 0, "matched": 0, "examples": []}

    result = await session.execute(
        select(User.user_id).where(User.user_id.in_(combot_ids))
    )
    our_ids = {row[0] for row in result.all()}

    by_id = {r["user_id"]: r for r in user_rows}
    matched = sorted(our_ids)
    examples = [
        {
            "user_id": uid,
            "username": by_id[uid].get("username"),
            "title": by_id[uid].get("title"),
            "messages": by_id[uid].get("messages"),
        }
        for uid in matched[:10]
    ]
    return {
        "combot_total": len(combot_ids),
        "matched": len(matched),
        "examples": examples,
    }


# --------------------------------------------------------------------------- #
# Главный поток
# --------------------------------------------------------------------------- #
def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


async def run_import(args: argparse.Namespace) -> int:
    settings = get_settings()
    api_key = os.environ.get("COMBOT_API_KEY", "").strip()
    if not api_key:
        print("ОШИБКА: переменная окружения COMBOT_API_KEY не задана.", file=sys.stderr)
        return 2

    chat_id = int(os.environ.get("COMBOT_CHAT_ID") or settings.chat_id)
    from_ms = args.from_ms or int(os.environ.get("COMBOT_FROM_MS") or DEFAULT_FROM_MS)
    to_ms = args.to_ms or _now_ms()

    print(f"Combot import: chat_id={chat_id} from_ms={from_ms} to_ms={to_ms}")
    if args.dry_run:
        print("РЕЖИМ DRY-RUN: запись в БД отключена.")

    # 1. Забираем данные из API (до открытия транзакции — сеть не должна
    #    держать БД-локи).
    print("Тяну channel_users ...")
    user_records = fetch_channel_users(chat_id, from_ms, to_ms, api_key)
    print(f"  получено записей пользователей: {len(user_records)}")

    print("Тяну channel_analytics ...")
    analytics = fetch_channel_analytics(chat_id, from_ms, to_ms, api_key)
    print(f"  messages_total: {analytics.get('messages_total')}")

    sessionmaker = get_sessionmaker()

    # DRY-RUN: считаем, печатаем, сверяем user_id — без записи.
    if args.dry_run:
        user_rows = build_user_rows(user_records, None)
        daily_rows = build_daily_rows(analytics, None)
        heatmap_rows = build_heatmap_rows(analytics, None)
        async with sessionmaker() as session:
            report = await match_users(session, user_rows)
        _print_summary(
            run_id=None,
            users=len(user_rows),
            days=len(daily_rows),
            cells=len(heatmap_rows),
            report=report,
            dry_run=True,
        )
        return 0

    # 2. Полный прогон с журналом combot_import_runs.
    run_id: int | None = None
    async with sessionmaker() as session:
        run = CombotImportRun(
            status="running",
            range_from_ms=from_ms,
            range_to_ms=to_ms,
            started_by=args.started_by,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        try:
            user_rows = build_user_rows(user_records, run_id)
            daily_rows = build_daily_rows(analytics, run_id)
            heatmap_rows = build_heatmap_rows(analytics, run_id)

            users_n = await upsert_users(session, user_rows)
            days_n = await upsert_daily(session, daily_rows)
            cells_n = await upsert_heatmap(session, heatmap_rows)

            report = await match_users(session, user_rows)

            run.users_imported = users_n
            run.days_imported = days_n
            run.heatmap_cells_imported = cells_n
            run.status = "success"
            run.finished_at = func.now()
            run.meta = {
                "messages_total": analytics.get("messages_total"),
                "matched_users": report["matched"],
            }
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            # Отдельной короткой транзакцией помечаем прогон как failed.
            async with sessionmaker() as fail_session:
                failed = await fail_session.get(CombotImportRun, run_id)
                if failed is not None:
                    failed.status = "failed"
                    failed.error = str(exc)[:512]
                    failed.finished_at = func.now()
                    await fail_session.commit()
            print(f"ОШИБКА импорта: {exc}", file=sys.stderr)
            return 1

    _print_summary(
        run_id=run_id,
        users=users_n,
        days=days_n,
        cells=cells_n,
        report=report,
        dry_run=False,
    )
    return 0


def _print_summary(*, run_id, users, days, cells, report, dry_run) -> None:
    head = "DRY-RUN ИТОГ" if dry_run else f"ИМПОРТ ЗАВЕРШЁН (run_id={run_id})"
    print("\n" + "=" * 60)
    print(head)
    print("=" * 60)
    print(f"  combot_user_stats:        {users} пользователей")
    print(f"  combot_daily_stats:       {days} дней")
    print(f"  combot_activity_heatmap:  {cells} ячеек")
    print("-" * 60)
    print(
        f"  сверка user_id: {report['matched']} из {report['combot_total']} "
        f"Combot-пользователей есть в нашей users"
    )
    if report["examples"]:
        print("  примеры совпадений (user_id | username | messages):")
        for ex in report["examples"]:
            uname = ex.get("username") or ex.get("title") or "—"
            print(f"    {ex['user_id']:>14}  {uname:<20}  {ex.get('messages')}")
    else:
        print("  совпадений с users не найдено.")
    print("=" * 60)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Одноразовый импорт исторической статистики Combot."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только запросить и показать статистику, без записи в БД.",
    )
    parser.add_argument(
        "--from-ms",
        type=int,
        default=0,
        help="Начало периода в Unix ms (по умолчанию 2020-01-01 / COMBOT_FROM_MS).",
    )
    parser.add_argument(
        "--to-ms",
        type=int,
        default=0,
        help="Конец периода в Unix ms (по умолчанию сейчас).",
    )
    parser.add_argument(
        "--started-by",
        type=int,
        default=None,
        help="user_id админа, запустившего импорт (для журнала).",
    )
    return parser.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        return await run_import(args)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
