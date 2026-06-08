"""Юнит-тесты компенсации при возврате кейсового приза (P0).

Чистая функция ``_case_prize_value`` (без БД): проверяем, что возврат
кейсового приза считает ПОЛНУЮ внутреннюю стоимость предмета
(``star_cost × ESHKI_PER_STAR``, Вариант А) и корректно деградирует, когда
star_cost берётся из слепка meta или вовсе неизвестен.

Запуск (в Docker, где есть Python и зависимости):

    docker compose exec bot pytest tests/test_gift_refund_value.py -q
"""

from __future__ import annotations

from dataclasses import dataclass

from app.features.gifts.service import _case_prize_value
from app.settings.balance import ESHKI_PER_STAR


@dataclass
class FakeDelivery:
    """Минимальный дубль GiftTransaction (нужен только meta)."""

    meta: dict | None


@dataclass
class FakeGift:
    """Минимальный дубль GiftCatalog (нужен только star_cost)."""

    star_cost: int | None


def test_value_from_live_catalog() -> None:
    # Сердечко: star_cost=15 → 150 ешек при ESHKI_PER_STAR=10.
    delivery = FakeDelivery(meta={"star_cost": 0})
    gift = FakeGift(star_cost=15)
    assert _case_prize_value(delivery, gift) == 15 * ESHKI_PER_STAR


def test_value_examples_match_spec() -> None:
    # Контрольные примеры из RELEASE 2.1 (при ESHKI_PER_STAR=10).
    cases = {
        15: 150,     # Сердечко
        25: 250,     # Роза
        50: 500,     # Ракета
        100: 1000,   # Бриллиант
        1000: 10000,  # Premium 3м
        1500: 15000,  # Premium 6м
    }
    for star_cost, expected in cases.items():
        delivery = FakeDelivery(meta=None)
        gift = FakeGift(star_cost=star_cost)
        assert _case_prize_value(delivery, gift) == expected


def test_value_falls_back_to_meta_snapshot() -> None:
    # Каталог удалён (gift=None) — берём star_cost из слепка meta доставки.
    delivery = FakeDelivery(meta={"star_cost": 50})
    assert _case_prize_value(delivery, None) == 50 * ESHKI_PER_STAR


def test_catalog_takes_priority_over_meta() -> None:
    # Живой каталог приоритетнее слепка meta.
    delivery = FakeDelivery(meta={"star_cost": 999})
    gift = FakeGift(star_cost=25)
    assert _case_prize_value(delivery, gift) == 25 * ESHKI_PER_STAR


def test_unknown_value_yields_zero() -> None:
    # Стоимость нигде не известна — 0 (компенсации не будет, в минус не уйдём).
    delivery = FakeDelivery(meta=None)
    assert _case_prize_value(delivery, None) == 0
    assert _case_prize_value(FakeDelivery(meta={}), FakeGift(star_cost=0)) == 0
