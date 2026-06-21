"""Pure-тесты идемпотентных DB prompt patches."""

from __future__ import annotations

from app.features.drun.prompt_upgrades import PATCHES, apply_patch_to_body


def test_prompt_patch_appends_marker_once():
    patch = PATCHES[0]
    body, changed = apply_patch_to_body("base", patch)
    assert changed is True
    assert "base" in body
    assert patch.marker in body

    body2, changed2 = apply_patch_to_body(body, patch)
    assert changed2 is False
    assert body2 == body


def test_all_prompt_patches_have_unique_markers():
    markers = [p.marker for p in PATCHES]
    assert len(markers) == len(set(markers))
