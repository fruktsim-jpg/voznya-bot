"""Тесты P2-петли обучения друна (чистая логика отклика/калибровки, без БД).

Сторожит две вынесенные функции:
* reflect._engagement_mark — метка «зашло/тишина» по числу ответов на реплику;
* worldview.calibration_hint — подсказка самокалибровки по hit-rate прогнозов.
"""
from __future__ import annotations

from app.features.drun import reflect as r
from app.features.drun import worldview as w


def test_engagement_mark_landed_silence_weak():
    assert "ЗАШЛО" in r._engagement_mark(3)
    assert "ЗАШЛО" in r._engagement_mark(10)
    assert "слабый" in r._engagement_mark(1)
    assert "слабый" in r._engagement_mark(2)
    assert "ТИШИНА" in r._engagement_mark(0)


def test_calibration_hint_needs_min_data():
    # Меньше 3 разрешённых прогнозов — не калибруемся.
    assert w.calibration_hint(0, 0) == ""
    assert w.calibration_hint(1, 1) == ""


def test_calibration_hint_high_rate_encourages():
    hint = w.calibration_hint(8, 2)  # 80%
    assert "80%" in hint
    assert "смелее" in hint


def test_calibration_hint_low_rate_warns():
    hint = w.calibration_hint(1, 9)  # 10%
    assert "10%" in hint
    assert "промахиваешься" in hint


def test_calibration_hint_mid_rate():
    hint = w.calibration_hint(5, 5)  # 50%
    assert "50%" in hint
    assert "средняя" in hint
