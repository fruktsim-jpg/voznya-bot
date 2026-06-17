"""Экспорт каталогов-источников истины в общий JSON-снимок для проверки паритета.

Бот — источник истины. Этот скрипт выгружает каталоги, которые ПРОДУБЛИРОВАНЫ
на сайте (v0-voznya), в ../docs/catalog-parity.json. И бот, и сайт затем
сверяются с этим снимком в своих тестах — любой рассинхрон ломает тест.

Запуск (из voznya-bot/):  python scripts/export_catalog_parity.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Запуск как скрипта: добавляем корень репозитория (voznya-bot/) в sys.path,
# чтобы импортировать пакет app без установки.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.permissions import ROLE_PERMISSIONS, ROLE_RANK
from app.settings.achievements import ACHIEVEMENTS
from app.settings.mmr import RANKS
from app.settings.season import DIVISIONS
from app.settings.titles import TITLES

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "catalog-parity.json"


def build() -> dict:
    return {
        "_note": (
            "AUTHORITATIVE snapshot generated from voznya-bot by "
            "scripts/export_catalog_parity.py. Bot is source of truth. The site "
            "(v0-voznya) mirrors these; both sides assert against this file. "
            "Regenerate after intentional catalog changes."
        ),
        "mmr_ranks": [
            {"minMmr": r.min_mmr, "emoji": r.emoji, "name": r.name} for r in RANKS
        ],
        "divisions": [
            {
                "minMmr": d.min_mmr,
                "emoji": d.emoji,
                "name": d.name,
                "rewardEshki": d.reward_eshki,
            }
            for d in DIVISIONS
        ],
        "titles": [
            {"minEarned": t.min_earned, "emoji": t.emoji, "name": t.name}
            for t in TITLES
        ],
        "achievements": [
            {
                "code": a.code,
                "emoji": a.emoji,
                "name": a.name,
                "description": a.description,
                "category": a.category,
                "reward": a.reward,
                "hidden": a.hidden,
            }
            for a in ACHIEVEMENTS
        ],
        "rbac": {
            "roleRank": dict(ROLE_RANK),
            "rolePermissions": {
                role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()
            },
        },
    }


def main() -> None:
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
