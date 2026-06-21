from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.features.drun import job_health


class Row:
    def __init__(self, **kwargs):
        self.job_name = kwargs.get("job_name", "job")
        self.last_run_at = kwargs.get("last_run_at")
        self.last_success_at = kwargs.get("last_success_at")
        self.last_error_at = kwargs.get("last_error_at")
        self.last_duration_ms = kwargs.get("last_duration_ms")
        self.last_rows = kwargs.get("last_rows")
        self.last_error = kwargs.get("last_error")
        self.runs = kwargs.get("runs", 0)
        self.successes = kwargs.get("successes", 0)
        self.failures = kwargs.get("failures", 0)
        self.updated_at = kwargs.get("updated_at", self.last_run_at)


def test_render_health_empty():
    assert "пока пуст" in job_health.render_health([])


def test_render_health_ok_and_error_rows():
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    rows = [
        Row(
            job_name="drun.archive.embedding_backfill",
            last_run_at=now - timedelta(minutes=5),
            last_success_at=now - timedelta(minutes=5),
            last_duration_ms=1234,
            last_rows=64,
            runs=3,
            successes=3,
        ),
        Row(
            job_name="drun.event_proposer",
            last_run_at=now - timedelta(minutes=7),
            last_success_at=now - timedelta(hours=1),
            last_error_at=now - timedelta(minutes=7),
            last_duration_ms=50,
            last_error="boom",
            runs=4,
            successes=3,
            failures=1,
        ),
    ]

    rendered = job_health.render_health(rows, now=now)

    assert "OK drun.archive.embedding_backfill" in rendered
    assert "rows=64" in rendered
    assert "ERR drun.event_proposer" in rendered
    assert "error: boom" in rendered
