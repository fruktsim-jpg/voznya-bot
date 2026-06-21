"""Pure-тесты ingest Telegram Desktop export."""

from __future__ import annotations

import json

from app.features.drun.telegram_export_ingest import (
    SOURCE,
    build_deterministic_proposals,
    chunk_range,
    collect_profile_aliases,
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


def test_chunk_range_supports_resumable_batches():
    messages = [object() for _ in range(100)]
    chunks = chunk_range(messages, chunk_size=10, start=3, count=2)
    assert len(chunks) == 2
    assert chunks[0][0] is messages[30]
    assert chunks[1][0] is messages[40]


# --- мост имён в профили (trusted owner-резолв по кличке из импорта) ----------


def _msg(mid: int, uid: int, name: str):
    from app.features.drun.telegram_export_ingest import ExportMessage

    return ExportMessage(message_id=mid, user_id=uid, name=name, text="x", dt=None)


def test_collect_profile_aliases_groups_names_by_user():
    messages = [
        _msg(1, 10, "Вася"),
        _msg(2, 10, "Вася"),
        _msg(3, 10, "Кот"),
        _msg(4, 10, "Кот"),
        _msg(5, 11, "Петя"),
        _msg(6, 11, "Петя"),
    ]
    out = collect_profile_aliases(messages)
    assert set(out) == {10, 11}
    # Имена, встреченные ≥2 раз, попадают; вес кодируется повторами для add_aliases.
    assert "Вася" in out[10] and "Кот" in out[10]
    assert out[11].count("Петя") >= 1


def test_collect_profile_aliases_drops_rare_names():
    # Разовое имя (опечатка/однодневная смена ника) не должно стать алиасом.
    messages = [
        _msg(1, 10, "Вася"),
        _msg(2, 10, "Вася"),
        _msg(3, 10, "Опечатка"),
    ]
    out = collect_profile_aliases(messages)
    assert "Опечатка" not in out.get(10, [])
    assert "Вася" in out[10]


def test_collect_profile_aliases_feeds_add_aliases_weight():
    # Контракт с add_aliases: повторы поднимают вес прозвища до потолка импорта.
    from app.features.drun import aliases as drun_aliases
    from app.features.drun.telegram_export_ingest import _ALIAS_MAX_IMPORT_WEIGHT

    messages = [_msg(i, 10, "Кот") for i in range(20)]
    out = collect_profile_aliases(messages)
    merged = drun_aliases.add_aliases(None, out[10])
    kot = next(a for a in merged if a["alias"] == "кот")
    assert kot["w"] == _ALIAS_MAX_IMPORT_WEIGHT
