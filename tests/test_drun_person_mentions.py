from __future__ import annotations

from app.features.drun import person_mentions as pm


class Row:
    id = 10
    source = "telegram_export"
    source_message_id = 100
    user_id = 1
    name = "speaker"
    text = "Карина сказала h1nt_jpg и @oew привет"
    message_at = None


def test_normalize_mention_strips_common_russian_suffix():
    assert pm.normalize_mention("Карине") == "карин"
    assert pm.normalize_mention("@oew") == "oew"


def test_extract_mentions_keeps_names_aliases_and_dedupes():
    out = pm.extract_mentions("Карина Карине h1nt_jpg @oew")
    norms = [x.mention_norm for x in out]

    assert "карин" in norms
    assert "h1nt_jpg" in norms
    assert "oew" in norms
    assert norms.count("карин") == 1


def test_rows_from_archive():
    rows = pm.rows_from_archive(Row())
    norms = {r["mention_norm"] for r in rows}

    assert "карин" in norms
    assert "h1nt_jpg" in norms
    assert rows[0]["archive_id"] == 10
    assert rows[0]["speaker_user_id"] == 1
