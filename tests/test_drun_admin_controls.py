"""Тесты белого списка/клампа управляемых настроек (admin_controls)."""

from __future__ import annotations

from app.features.drun import admin_controls as ac


def test_unknown_key_not_writable():
    assert ac.is_writable("totally.unknown") is False
    assert ac.is_writable("casino.enabled") is True


def test_coerce_bool_from_strings():
    spec = ac._WRITABLE["casino.enabled"]
    assert ac._coerce(spec, "on") is True
    assert ac._coerce(spec, "выкл") is False
    assert ac._coerce(spec, 0) is False
    assert ac._coerce(spec, True) is True


def test_coerce_int_clamps_to_range():
    spec = ac._WRITABLE["casino.max_bet"]
    # Выше потолка — прижимается к hi.
    assert ac._coerce(spec, 10**12) == int(spec.hi)
    # Ниже пола — прижимается к lo.
    assert ac._coerce(spec, -50) == int(spec.lo)
    assert ac._coerce(spec, 5000) == 5000


def test_coerce_float_multiplier_clamped():
    spec = ac._WRITABLE["modifier.eshki"]
    assert ac._coerce(spec, 99) == 5.0       # hi
    assert ac._coerce(spec, 0.0) == 0.1      # lo
    assert ac._coerce(spec, 2.5) == 2.5


def test_coerce_rejects_garbage():
    spec = ac._WRITABLE["casino.max_bet"]
    assert ac._coerce(spec, "abc") is None
