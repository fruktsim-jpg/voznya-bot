"""Persistent health tracking for Drun/background jobs."""

from __future__ import annotations

import time
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AiJobHealth

_T = TypeVar("_T")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err_text(exc: BaseException) -> str:
    text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return text[:2000]


async def record_success(
    session: AsyncSession,
    job_name: str,
    *,
    duration_ms: int,
    rows: int | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    ts = _now()
    stmt = insert(AiJobHealth).values(
        job_name=job_name,
        last_run_at=ts,
        last_success_at=ts,
        last_duration_ms=duration_ms,
        last_rows=rows,
        last_error=None,
        runs=1,
        successes=1,
        failures=0,
        meta=meta or {},
        updated_at=ts,
    ).on_conflict_do_update(
        index_elements=[AiJobHealth.job_name],
        set_={
            "last_run_at": ts,
            "last_success_at": ts,
            "last_duration_ms": duration_ms,
            "last_rows": rows,
            "last_error": None,
            "runs": AiJobHealth.runs + 1,
            "successes": AiJobHealth.successes + 1,
            "meta": meta or {},
            "updated_at": ts,
        },
    )
    await session.execute(stmt)


async def record_failure(
    session: AsyncSession,
    job_name: str,
    *,
    duration_ms: int,
    error: BaseException | str,
    meta: dict[str, Any] | None = None,
) -> None:
    ts = _now()
    err = _err_text(error) if isinstance(error, BaseException) else str(error)[:2000]
    stmt = insert(AiJobHealth).values(
        job_name=job_name,
        last_run_at=ts,
        last_error_at=ts,
        last_duration_ms=duration_ms,
        last_error=err,
        runs=1,
        successes=0,
        failures=1,
        meta=meta or {},
        updated_at=ts,
    ).on_conflict_do_update(
        index_elements=[AiJobHealth.job_name],
        set_={
            "last_run_at": ts,
            "last_error_at": ts,
            "last_duration_ms": duration_ms,
            "last_error": err,
            "runs": AiJobHealth.runs + 1,
            "failures": AiJobHealth.failures + 1,
            "meta": meta or {},
            "updated_at": ts,
        },
    )
    await session.execute(stmt)


async def run_tracked(
    session: AsyncSession,
    job_name: str,
    fn: Callable[[], Awaitable[_T]],
    *,
    rows_from_result: Callable[[_T], int | None] | None = None,
    meta: dict[str, Any] | None = None,
) -> _T:
    started = time.perf_counter()
    try:
        result = await fn()
    except Exception as exc:  # noqa: BLE001
        duration = int((time.perf_counter() - started) * 1000)
        await record_failure(session, job_name, duration_ms=duration, error=exc, meta=meta)
        await session.commit()
        raise
    duration = int((time.perf_counter() - started) * 1000)
    rows = rows_from_result(result) if rows_from_result else None
    await record_success(
        session,
        job_name,
        duration_ms=duration,
        rows=rows,
        meta=meta,
    )
    await session.commit()
    return result


def tracked_job(
    job_name: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    fn: Callable[[AsyncSession], Awaitable[_T]],
    *,
    rows_from_result: Callable[[_T], int | None] | None = None,
    meta: dict[str, Any] | None = None,
) -> Callable[[], Awaitable[_T]]:
    async def _wrapped() -> _T:
        async with sessionmaker() as session:
            return await run_tracked(
                session,
                job_name,
                lambda: fn(session),
                rows_from_result=rows_from_result,
                meta=meta,
            )

    return _wrapped


async def list_health(session: AsyncSession, *, limit: int = 30) -> list[AiJobHealth]:
    return list((await session.execute(
        select(AiJobHealth).order_by(AiJobHealth.updated_at.desc()).limit(limit)
    )).scalars().all())


def render_health(rows: list[AiJobHealth], *, now: datetime | None = None) -> str:
    if not rows:
        return "Job health пока пуст: джобы ещё не успели отметиться."
    current = now or _now()
    lines = ["# JOB HEALTH"]
    for row in rows:
        ok = row.last_success_at and (
            row.last_error_at is None or row.last_success_at >= row.last_error_at
        )
        mark = "OK" if ok else "ERR"
        last = row.last_run_at or row.updated_at
        age = "?"
        if last:
            try:
                age_s = int((current - last).total_seconds())
                age = f"{max(0, age_s // 60)}m ago"
            except Exception:  # noqa: BLE001
                age = "?"
        rows_s = "" if row.last_rows is None else f", rows={row.last_rows}"
        dur_s = "" if row.last_duration_ms is None else f", {row.last_duration_ms}ms"
        lines.append(
            f"- {mark} {row.job_name}: {age}{dur_s}{rows_s}; "
            f"runs={row.runs}, ok={row.successes}, fail={row.failures}"
        )
        if row.last_error and not ok:
            lines.append(f"  error: {row.last_error[:240]}")
    return "\n".join(lines)
