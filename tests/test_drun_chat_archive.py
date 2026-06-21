from __future__ import annotations

from datetime import datetime, timezone

from app.features.drun import chat_archive as a
from app.features.drun.telegram_export_ingest import ExportMessage


def test_archive_rows_from_export_normalizes_and_keeps_metadata():
    msg = ExportMessage(
        message_id=123,
        user_id=10,
        name="Очень Длинное Имя" * 20,
        text="  привет   старый   чат  ",
        dt=datetime(2026, 6, 1, tzinfo=timezone.utc),
        reply_to_message_id=77,
    )

    rows = a.archive_rows_from_export([msg])

    assert len(rows) == 1
    row = rows[0]
    assert row["source_message_id"] == 123
    assert row["user_id"] == 10
    assert row["text"] == "привет старый чат"
    assert row["meta"] == {"reply_to_message_id": 77}
    assert len(row["name"]) == 96


def test_archive_rows_from_export_skips_empty_text():
    rows = a.archive_rows_from_export([
        ExportMessage(1, 10, "x", "   ", None),
        ExportMessage(2, 10, "x", "ok", None),
    ])

    assert [r["source_message_id"] for r in rows] == [2]


def test_render_archive_hits_uses_real_dates_and_truncates():
    hits = [
        a.ArchiveHit(
            id=1,
            user_id=10,
            name="h1nt",
            text="x" * 260,
            message_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
    ]

    rendered = a.render_archive_hits(hits)

    assert "# СЫРОЙ АРХИВ ЧАТА" in rendered
    assert "[2026-06-01] h1nt:" in rendered
    assert "…" in rendered
    assert len(rendered.splitlines()[1]) < 270


def test_render_archive_hits_empty():
    assert a.render_archive_hits([]) == ""


def test_live_archive_row_uses_export_namespace_for_dedupe():
    row = a.live_archive_row(
        message_id=555,
        user_id=10,
        name="h1nt",
        text=" новое   живое сообщение ",
        message_at=datetime(2026, 6, 21, tzinfo=timezone.utc),
        reply_to_message_id=500,
    )

    assert row is not None
    assert row["source"] == "telegram_export"
    assert row["source_message_id"] == 555
    assert row["text"] == "новое живое сообщение"
    assert row["meta"] == {"live": True, "reply_to_message_id": 500}


def test_live_archive_row_keeps_media_placeholder():
    row = a.live_archive_row(
        message_id=556,
        user_id=10,
        name="h1nt",
        text="",
        message_at=None,
        media="sticker",
    )

    assert row is not None
    assert row["text"] == "[sticker]"
    assert row["meta"] == {"live": True, "media": "sticker"}


def test_diversify_archive_hits_moves_recent_ids_back():
    hits = [
        a.ArchiveHit(1, 10, "a", "old repeated", None, score=10.0),
        a.ArchiveHit(2, 10, "a", "fresh", None, score=5.0),
    ]

    selected = a.diversify_archive_hits(hits, recent_archive_ids=[1], limit=2)

    assert [h.id for h in selected] == [2, 1]
