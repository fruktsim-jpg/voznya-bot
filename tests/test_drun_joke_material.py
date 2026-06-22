from __future__ import annotations

from app.features.drun import joke_material as jm


def test_clean_query_removes_joke_prefix():
    assert jm._clean_query("пошути про Карину") == "Карину"
    assert jm._clean_query("расскажи анекдот") == ""


def test_bad_stale_material_filters_economy_when_not_requested():
    assert jm._bad_stale_material("проиграл все ешки в дуэли", query="расскажи анекдот") is True
    assert jm._bad_stale_material("проиграл все ешки в дуэли", query="шутка про ешки") is False


def test_render_joke_materials():
    block = jm.render_joke_materials([
        jm.JokeMaterial(kind="world_lore", text="чайник стал мэром", source="ai_memories")
    ])

    assert "# МАТЕРИАЛ ДЛЯ ШУТКИ" in block
    assert "чайник стал мэром" in block
    assert "ешки/казино/дуэли" in block
