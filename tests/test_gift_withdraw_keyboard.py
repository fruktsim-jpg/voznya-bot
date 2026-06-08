"""Юнит-тесты клавиатур выбора судьбы подарка (P1/P2/P6).

Чистые функции построения inline-клавиатур (без БД и Telegram): проверяем, что
экран выбора после выпадения подарка содержит «Оставить / Продать / Вывести», а
кнопка повтора выдачи бьёт в тот же ``gift:withdraw`` callback.

Запуск (в Docker):

    docker compose exec bot pytest tests/test_gift_withdraw_keyboard.py -q
"""

from __future__ import annotations

from app.core.keyboards import case_gift_choice, gift_retry


def _flatten(markup) -> list:
    """Все кнопки клавиатуры одним списком (для удобных проверок)."""
    return [btn for row in markup.inline_keyboard for btn in row]


def test_choice_without_withdraw_has_two_actions() -> None:
    # Без withdraw_label — только «Оставить» и «Продать» (обратная совместимость).
    markup = case_gift_choice(
        "casegift:1:abc",
        1,
        175,
        keep_label="Оставить",
        sell_label="Продать за {amount}",
    )
    buttons = _flatten(markup)
    assert len(buttons) == 2
    assert buttons[0].callback_data == "gift:keep:casegift:1:abc:1"
    assert buttons[1].callback_data == "gift:sell:casegift:1:abc:1"
    # {amount} подставляется суммой продажи.
    assert "175" in buttons[1].text


def test_choice_with_withdraw_adds_third_action() -> None:
    # С withdraw_label появляется «Вывести» → gift:withdraw (P2).
    markup = case_gift_choice(
        "casegift:7:def",
        7,
        700,
        keep_label="Оставить",
        sell_label="Продать за {amount}",
        withdraw_label="Вывести",
    )
    buttons = _flatten(markup)
    assert len(buttons) == 3
    assert buttons[2].callback_data == "gift:withdraw:casegift:7:def:7"
    assert buttons[2].text == "Вывести"


def test_gift_retry_targets_withdraw() -> None:
    # Кнопка повтора (P6) ведёт в тот же gift:withdraw, что и «Вывести».
    markup = gift_retry("casegift:9:xyz", 9, retry_label="Ещё раз")
    buttons = _flatten(markup)
    assert len(buttons) == 1
    assert buttons[0].callback_data == "gift:withdraw:casegift:9:xyz:9"
    assert buttons[0].text == "Ещё раз"
