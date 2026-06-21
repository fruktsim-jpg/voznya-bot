"""Pure-тесты ingest Telegram Desktop export."""

from __future__ import annotations

import json

from app.features.drun.telegram_export_ingest import (
    SOURCE,
    build_deterministic_proposals,
    filter_messages,
    load_export_messages,
    normalize_text,
    selected_chunks,
)


def test_normalize_text_handles_entity_list():
    text = normalize_text([
        "привет ",
        {"type": "bold", "text": "друн"},
        {"type": "plain", "text": "  как дела"},
    ])
    assert text == "привет друн как дела"


def test_load_export_messages_skips_service_and_empty(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({
        "messages": [
            {"id": 1, "type": "service", "text": ""},
            {"id": 2, "type": "message", "from": "Вася", "from_id": "user10", "text": "чина", "date_unixtime": "100"},
            {"id": 3, "type": "message", "from": "Петя", "from_id": "user11", "text": ""},
        ]
    }), encoding="utf-8")

    messages = load_export_messages(path)
    assert len(messages) == 1
    assert messages[0].message_id == 2
    assert messages[0].user_id == 10
    assert messages[0].name == "Вася"
    assert messages[0].text == "чина"


def test_build_deterministic_proposals_from_export(tmp_path):
    path = tmp_path / "result.json"
    rows = []
    for i in range(25):
        rows.append({
            "id": i + 1,
            "type": "message",
            "from": "Вася",
            "from_id": "user10",
            "text": "чина",
            "date_unixtime": str(100 + i),
        })
    path.write_text(json.dumps({"messages": rows}), encoding="utf-8")
    messages = load_export_messages(path)
    proposals = build_deterministic_proposals(messages)

    assert any(p.subject_id is None and p.kind == "legend" for p in proposals)
    assert any(p.subject_id == 10 and p.kind == "trait" for p in proposals)
    meme = [p for p in proposals if p.kind == "chat:meme"][0]
    assert meme.source == SOURCE
    assert "чина" in meme.fact


def test_filter_messages_excludes_bot_id(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({
        "messages": [
            {"id": 1, "type": "message", "from": "Тёмный друн", "from_id": "user8785112116", "text": "я друн"},
            {"id": 2, "type": "message", "from": "Вася", "from_id": "user10", "text": "чина"},
        ]
    }), encoding="utf-8")
    messages = load_export_messages(path)
    filtered = filter_messages(messages, exclude_user_ids={8785112116})
    assert len(filtered) == 1
    assert filtered[0].user_id == 10


def test_selected_chunks_samples_full_timeline():
    messages = [object() for _ in range(100)]
    chunks = selected_chunks(messages, chunk_size=10, max_chunks=3)
    assert chunks[0][0] is messages[0]
    assert chunks[1][0] is messages[40] or chunks[1][0] is messages[50]
    assert chunks[2][0] is messages[90]
