from __future__ import annotations

from app.features.drun import joke_style as js


def test_select_joke_style_avoids_recent_styles():
    all_keys = [s.key for s in js.available_styles()]
    selected = js.select_joke_style("расскажи анекдот", recent_styles=all_keys[:-1])

    assert selected.key == all_keys[-1]


def test_select_joke_style_falls_back_if_all_recent():
    all_keys = [s.key for s in js.available_styles()]
    selected = js.select_joke_style("расскажи анекдот", recent_styles=all_keys)

    assert selected.key in all_keys


def test_render_joke_style_contains_format_and_bans_stale_fallback():
    style = js.available_styles()[0]
    block = js.render_joke_style(style)

    assert "# ФОРМА ШУТКИ" in block
    assert style.key in block
    assert "Не объясняй шутку" in block
    assert "ешки/дуэли/КД" in block
