"""Тесты парсера уроков самообучения (чистая логика, без БД/LLM)."""

from __future__ import annotations

from app.features.drun import reflect as r


def test_parse_valid_lessons():
    raw = (
        'тут немного текста [{"lesson":"67 — это местный знак удачи",'
        '"weight":3},{"lesson":"шутки про передоз заходят","weight":2}] хвост'
    )
    out = r._parse_lessons(raw)
    assert len(out) == 2
    assert out[0]["lesson"].startswith("67")
    assert out[0]["weight"] == 3


def test_parse_empty_and_garbage():
    assert r._parse_lessons("") == []
    assert r._parse_lessons("no json here") == []
    assert r._parse_lessons("[]") == []


def test_parse_clamps_weight_and_skips_bad():
    raw = '[{"lesson":"норм урок","weight":9},{"lesson":"","weight":1},{"x":1}]'
    out = r._parse_lessons(raw)
    assert len(out) == 1
    assert out[0]["weight"] == 3  # 9 → clamp 3


def test_parse_drops_overlong():
    long = "x" * 300
    out = r._parse_lessons(f'[{{"lesson":"{long}","weight":1}}]')
    assert out == []


def test_parse_caps_per_run():
    items = ",".join(
        f'{{"lesson":"урок {i}","weight":1}}' for i in range(20)
    )
    out = r._parse_lessons(f"[{items}]")
    assert len(out) <= r._PER_RUN
