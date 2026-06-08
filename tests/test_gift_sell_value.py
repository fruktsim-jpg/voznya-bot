"""Юнит-тесты стоимости продажи предмета (P5).

Чистые функции ``_item_full_value`` и ``_sell_value`` (без БД): проверяем
базу стоимости (цена покупки магазина vs внутренняя стоимость кейсового приза)
и расчёт выплаты по ``ITEM_SELL_RATE`` (по умолчанию 70%).

Запуск (в Docker):

    docker compose exec bot pytest tests/test_gift_sell_value.py -q
"""

from __future__ import annotations

from dataclasses import dataclass

from app.features.gifts.service import _item_full_value, _sell_value
from app.settings.balance import ESHKI_PER_STAR, ITEM_SELL_RATE


@dataclass
class FakeDelivery:
    """Дубль GiftTransaction: transaction_id отличает покупку от приза кейса."""

    transaction_id: int | None
    meta: dict | None = None


@dataclass
class FakeGift:
    """Дубль GiftCatalog (нужны star_cost и price_eshki)."""

    star_cost: int | None = None
    price_eshki: int | None = None


def test_sell_value_examples_match_spec() -> None:
    # Контрольные примеры из RELEASE 2.1 при ITEM_SELL_RATE=0.70.
    assert ITEM_SELL_RATE == 0.70
    assert _sell_value(150) == 105    # Сердечко
    assert _sell_value(250) == 175    # Роза
    assert _sell_value(500) == 350    # Ракета
    assert _sell_value(1000) == 700   # Бриллиант
    assert _sell_value(10000) == 7000  # Premium 3м
    assert _sell_value(15000) == 10500  # Premium 6м


def test_sell_value_floors() -> None:
    # 333 × 0.70 = 233.1 → 233 (floor через int()).
    assert _sell_value(333) == 233
    assert _sell_value(0) == 0
    assert _sell_value(-100) == 0


def test_full_value_uses_shop_price_for_both_sources() -> None:
    # Release 2.2: ЕДИНЫЙ курс — база всегда цена магазина (price_eshki),
    # независимо от источника предмета (покупка магазина ИЛИ приз кейса).
    shop = FakeDelivery(transaction_id=42, meta={"star_cost": 999})
    prize = FakeDelivery(transaction_id=None, meta=None)
    gift = FakeGift(star_cost=25, price_eshki=300)
    assert _item_full_value(shop, gift) == 300
    assert _item_full_value(prize, gift) == 300  # тот же курс, что в магазине


def test_full_value_falls_back_to_internal_when_no_price() -> None:
    # price_eshki не задан → фолбэк на внутреннюю стоимость star_cost × курс.
    delivery = FakeDelivery(transaction_id=None, meta=None)
    gift = FakeGift(star_cost=25, price_eshki=None)
    assert _item_full_value(delivery, gift) == 25 * ESHKI_PER_STAR


def test_full_value_case_prize_falls_back_to_meta() -> None:
    # Каталог удалён — star_cost берётся из слепка meta.
    delivery = FakeDelivery(transaction_id=None, meta={"star_cost": 50})
    assert _item_full_value(delivery, None) == 50 * ESHKI_PER_STAR


