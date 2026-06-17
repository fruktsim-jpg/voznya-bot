"""Снимок паритета каталогов должен быть пересгенерирован из текущего бота.

Бот — источник истины; сайт сверяется с ../docs/catalog-parity.json. Если кто-то
меняет каталог бота (ачивки/титулы/ранги/дивизионы/права), но забывает обновить
снимок, сайт никогда не узнает о рассинхроне. Этот тест ловит такую ситуацию:
сравнивает свежесобранные каталоги с закоммиченным JSON.

Если упал — выполни:  python scripts/export_catalog_parity.py
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.export_catalog_parity import build

SNAPSHOT = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "catalog-parity.json"
)


def test_committed_snapshot_matches_current_catalogs():
    assert SNAPSHOT.exists(), (
        "docs/catalog-parity.json отсутствует — запусти "
        "python scripts/export_catalog_parity.py"
    )
    committed = json.loads(SNAPSHOT.read_text("utf-8"))
    fresh = build()
    assert committed == fresh, (
        "Снимок паритета устарел относительно каталогов бота. "
        "Перегенерируй: python scripts/export_catalog_parity.py"
    )
