"""Хендлеры кейсов: /кейсы, /кейс, /открыть и кнопка открытия.

Открытие идёт ТОЛЬКО через :func:`app.features.cases.service.open_case` —
единственную атомарную точку выдачи. Хендлеры лишь валидируют ввод, проверяют
владельца кнопки и рендерят результат.
"""

from __future__ import annotations

import asyncio

from aiogram import F, Router

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand

from app.config import get_settings
from app.core.keyboards import case_gift_choice, case_open, gift_retry, open_on_site

from app.core.money import money
from app.core.responses import notify_and_cleanup
from app.features.cases.events import CaseOpenEvent, emit_case_opened
from app.features.cases.service import OpenResult, open_case
from app.features.gifts.service import deliver_gift, sell_gift

from app.models import CaseReward, Inventory
from app.repositories import cases as cases_repo
from app.repositories import gifts as gifts_repo
from app.settings import inventory as inv_texts

from app.settings import texts


router = Router(name="cases")



async def _owned_count(session: AsyncSession, user_id: int, item_code: str) -> int:
    """Сколько у игрока этого предмета-кейса (0, если нет)."""
    qty = await session.scalar(
        select(Inventory.quantity)
        .where(Inventory.user_id == user_id)
        .where(Inventory.item_code == item_code)
    )
    return int(qty or 0)


def _cost_label(case) -> str:
    """Человекочитаемая стоимость открытия кейса."""
    if case.open_cost_kind == "currency" and case.open_cost_amount > 0:
        return money(case.open_cost_amount)
    return texts.CASES_ROW_FREE


@router.message(RuCommand("кейсы", "cases"))
async def cmd_cases(message: Message, session: AsyncSession) -> None:
    """Site-first (Release 2.2): кейсы открываются на сайте, не в Telegram.

    Команда больше не обслуживает открытие внутри бота — показывает карточку с
    кнопкой на страницу кейсов. Это убирает дублирование тяжёлой механики в двух
    интерфейсах (единый центр опыта — сайт).
    """
    if message.from_user is None:
        return
    url = f"{get_settings().website_url}/cases"
    await message.answer(
        texts.CASES_SITE_CARD,
        reply_markup=open_on_site(texts.CASES_SITE_BTN, url),
    )



async def _best_reward_label(session: AsyncSession, case_item_code: str) -> str | None:
    """Подпись самой ценной награды кейса (по редкости, джекпоты в приоритете).

    Возвращает строку с эмодзи редкости и названием предмета (без шанса) или
    None, если у кейса нет наград. Используется в списке кейсов как превью.
    """
    rewards = await cases_repo.get_case_rewards(session, case_item_code)
    if not rewards:
        return None
    item_codes = [
        r.reward_item_code
        for r in rewards
        if r.reward_kind == "item" and r.reward_item_code
    ]
    item_meta = await cases_repo.get_item_meta_by_codes(session, item_codes)
    gift_codes = [
        r.reward_item_code
        for r in rewards
        if r.reward_kind == "tg_gift" and r.reward_item_code
    ]
    gift_meta = await cases_repo.get_gift_meta_by_codes(session, gift_codes)

    def _key(r: CaseReward) -> tuple:
        rarity = _reward_rarity(r, item_meta)
        order = inv_texts.rarity_style(rarity).order
        # tg_gift — самые ценные награды (реальные подарки/Premium): выше всего.
        kind_rank = 1 if r.reward_kind == "tg_gift" else 0
        return (kind_rank, 1 if r.is_jackpot else 0, order)

    best = max(rewards, key=_key)
    return _reward_label(best, item_meta, gift_meta)




def _format_chance(weight: int, total: int) -> str:
    """Форматирует шанс выпадения в проценты."""
    if total <= 0:
        return "—"
    pct = weight / total * 100
    return f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%"


# Порядок редкости валюты в дроп-листе: ешки показываем среди «обычных».
_CURRENCY_RARITY = "common"


def _reward_rarity(reward: CaseReward, item_meta: dict[str, tuple[str, str]]) -> str:
    """Редкость награды: предмет — из каталога, tg_gift — легендарный, ешки —
    common. Реальные Telegram Gifts/Premium — самые ценные, поэтому group'аются
    как «легендарные» в дроп-листе."""
    if reward.reward_kind == "item" and reward.reward_item_code:
        meta = item_meta.get(reward.reward_item_code)
        if meta is not None:
            return meta[1]
    if reward.reward_kind == "tg_gift":
        return "legendary"
    return _CURRENCY_RARITY


def _reward_label(
    reward: CaseReward,
    item_meta: dict[str, tuple[str, str]],
    gift_meta: dict[str, str] | None = None,
) -> str:
    """Подпись награды с эмодзи редкости и НАЗВАНИЕМ (код — только фолбэк)."""
    qty_suffix = ""
    if reward.max_qty > reward.min_qty:
        qty_suffix = f" ×{reward.min_qty}–{reward.max_qty}"
    elif reward.min_qty > 1:
        qty_suffix = f" ×{reward.min_qty}"

    if reward.reward_kind == "currency":
        style = inv_texts.rarity_style(_CURRENCY_RARITY)
        return f"{style.emoji} {money(reward.amount or 0)}"

    if reward.reward_kind == "item":
        rarity = _reward_rarity(reward, item_meta)
        style = inv_texts.rarity_style(rarity)
        name = None
        if reward.reward_item_code:
            meta = item_meta.get(reward.reward_item_code)
            name = meta[0] if meta else reward.reward_item_code
        name = name or "предмет"
        return f"{style.emoji} <b>{name}</b>{qty_suffix}"

    if reward.reward_kind == "tg_gift":
        # Реальный Telegram Gift / Premium: 🎁 + название из каталога.
        name = None
        if reward.reward_item_code:
            name = (gift_meta or {}).get(reward.reward_item_code)
            name = name or reward.reward_item_code
        name = name or "подарок"
        return f"🎁 <b>{name}</b>"

    return reward.reward_kind



@router.message(RuCommand("кейс", "case"))
async def cmd_case(message: Message, session: AsyncSession, command_args: str) -> None:
    """Карточка одного кейса: описание, дроп-лист с шансами, кнопка открытия.

    Подача: награды отсортированы от редких к обычным, у каждой — эмодзи
    редкости и человекочитаемое название (item_code только как фолбэк).
    Джекпоты вынесены отдельной строкой, чтобы игрок сразу видел «ради чего».
    """
    user = message.from_user
    if user is None:
        return

    code = command_args.split()[0] if command_args else ""
    if not code:
        await notify_and_cleanup(session, message, texts.CASE_USAGE)
        return

    case = await cases_repo.get_case_by_item_code(session, code)
    if case is None or not case.is_active:
        await notify_and_cleanup(session, message, texts.CASE_NOT_FOUND)
        return

    rewards = await cases_repo.get_case_rewards(session, code)
    total = sum(r.weight for r in rewards) or 1

    # Подтягиваем названия/редкости предметов одним запросом (фолбэк — код).
    item_codes = [
        r.reward_item_code
        for r in rewards
        if r.reward_kind == "item" and r.reward_item_code
    ]
    item_meta = await cases_repo.get_item_meta_by_codes(session, item_codes)
    gift_codes = [
        r.reward_item_code
        for r in rewards
        if r.reward_kind == "tg_gift" and r.reward_item_code
    ]
    gift_meta = await cases_repo.get_gift_meta_by_codes(session, gift_codes)

    # Сортировка: джекпоты выше, затем по убыванию редкости, затем по шансу.
    def _sort_key(r: CaseReward) -> tuple:
        rarity = _reward_rarity(r, item_meta)
        order = inv_texts.rarity_style(rarity).order
        return (0 if r.is_jackpot else 1, -order, r.weight)


    ordered = sorted(rewards, key=_sort_key)

    body = [
        texts.CASE_CARD_HEADER.format(
            name=case.name,
            description=case.description or "",
            cost=_cost_label(case),
        )
    ]

    # Джекпот-подсказка: что самое жирное можно выбить (если есть лимитки).
    jackpots = [r for r in ordered if r.is_jackpot]
    if jackpots:
        labels = ", ".join(
            _reward_label(r, item_meta, gift_meta).replace("<b>", "").replace("</b>", "")
            for r in jackpots[:3]
        )
        body.append(texts.CASE_CARD_JACKPOT.format(rewards=labels))


    # Группируем по редкости с заголовками — визуально отделяем ценное от
    # ширпотреба. Внутри группы порядок уже задан сортировкой ordered.
    current_rarity: str | None = None
    for r in ordered:
        rarity = _reward_rarity(r, item_meta)
        if rarity != current_rarity:
            current_rarity = rarity
            style = inv_texts.rarity_style(rarity)
            body.append(
                texts.CASE_CARD_RARITY_HEADER.format(
                    emoji=style.emoji, name=style.name
                )
            )
        prefix = "💎 " if r.is_jackpot else ""
        body.append(
            texts.CASE_CARD_ROW.format(
                label=prefix + _reward_label(r, item_meta),
                chance=_format_chance(r.weight, total),
            )
        )
    owned = await _owned_count(session, user.id, case.item_code)
    body.append(texts.CASE_CARD_FOOTER.format(count=owned))

    # Site-first (Release 2.2): дроп-лист — это просмотр данных (ок для бота), но
    # само открытие живёт на сайте. Кнопка ведёт на страницу кейсов.
    url = f"{get_settings().website_url}/cases"
    await message.answer(
        "\n".join(body), reply_markup=open_on_site(texts.CASES_SITE_BTN, url)
    )




def _render_open(
    result: OpenResult, user_id: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Рендерит результат успешного открытия: текст + опц. клавиатура выбора.

    Для tg_gift показываем экран выбора «Оставить / Продать» (P1/P7): игрок
    решает судьбу подарка сразу, не уходя из чата.
    """
    markup: InlineKeyboardMarkup | None = None
    if result.reward_kind == "currency":
        line = texts.CASE_OPEN_WIN_CURRENCY.format(
            case=result.case_name,
            amount=money(result.amount or 0),
            balance=money(result.balance or 0),
        )
    elif result.reward_kind == "tg_gift":
        # Реальный Telegram Gift / Premium: экран выбора (оставить/продать).
        # Только человекочитаемое имя; код (gift_bear) пользователю не показываем.
        gift = result.reward_item_name or "подарок"
        value = money(result.reward_value or 0)

        line = texts.CASE_OPEN_WIN_GIFT.format(
            case=result.case_name, gift=gift, value=value
        )
        # Кнопки выбора привязаны к ключу pending-доставки и владельцу.
        if result.delivery_key:
            markup = case_gift_choice(
                result.delivery_key,
                user_id,
                result.reward_sell_amount or 0,
                keep_label=texts.CASE_GIFT_KEEP_BTN,
                sell_label=texts.CASE_GIFT_SELL_BTN,
                withdraw_label=texts.CASE_GIFT_WITHDRAW_BTN,
            )

    else:


        qty = f" ×{result.qty}" if result.qty > 1 else ""
        # Показываем редкость предмета (эмодзи + название) — игроку важно
        # сразу понять, насколько ценный дроп выпал. Раньше rarity было пустым.
        if result.reward_rarity:
            style = inv_texts.rarity_style(result.reward_rarity)
            rarity = f"{style.emoji} {style.name} "
        else:
            rarity = ""
        line = texts.CASE_OPEN_WIN_ITEM.format(
            case=result.case_name,
            rarity=rarity,
            # Только название; внутренний код предмета пользователю не показываем.
            item=result.reward_item_name or "предмет",
            qty=qty,
        )


    if result.is_jackpot:
        line = texts.CASE_OPEN_JACKPOT.format(line=line)
    return line, markup



def _render_failure(result: OpenResult) -> str | None:
    """Текст для неуспешного исхода (или None, если исход успешный)."""
    if result.status == "no_key":
        return texts.CASE_OPEN_NO_KEY
    if result.status == "not_enough":
        return texts.CASE_OPEN_NOT_ENOUGH.format(name=result.case_name)
    if result.status == "inactive":
        return texts.CASE_OPEN_INACTIVE.format(name=result.case_name)
    if result.status in ("not_found",):
        return texts.CASE_NOT_FOUND
    if result.status != "ok":
        return texts.CASE_OPEN_ERROR
    return None


async def _do_open_and_render(
    session: AsyncSession, user_id: int, code: str
) -> tuple[str, InlineKeyboardMarkup | None, OpenResult]:
    """Открывает кейс и возвращает (текст, клавиатура, результат) для рендера."""
    result = await open_case(session, user_id=user_id, case_item_code=code)
    failure = _render_failure(result)
    if failure is not None:
        return failure, None, result

    # Событие для будущих достижений (best-effort, не ломает открытие).
    total = await cases_repo.count_openings(session, user_id)
    await emit_case_opened(
        CaseOpenEvent(
            user_id=user_id,
            case_item_code=code,
            reward_kind=result.reward_kind,
            reward_item_code=result.reward_item_code,
            reward_rarity=result.reward_rarity,
            amount=result.amount,
            is_jackpot=result.is_jackpot,
            total_openings=total,
        )
    )
    text, markup = _render_open(result, user_id)
    return text, markup, result



async def _open_with_animation(
    session: AsyncSession, anchor: Message, user_id: int, code: str
) -> None:
    """Открывает кейс с лёгкой «анимацией» в ОДНОМ сообщении.

    Чисто UX: ни экономика, ни RNG, ни выдача не трогаются. Шлём первый кадр,
    параллельно крутим открытие (open_case) и подменяем текст кадрами саспенса,
    затем редактируем сообщение финальным результатом. Если редактирование
    недоступно (старое сообщение/ограничение Telegram) — мягкий фолбэк на
    обычный ответ. Общий цикл ≈ len(FRAMES) × FRAME_DELAY (около 1.2–1.6 c).
    """
    frames = texts.CASE_OPEN_FRAMES
    delay = texts.CASE_OPEN_FRAME_DELAY

    # Первый кадр — сразу, чтобы игрок видел реакцию мгновенно.
    try:
        bubble = await anchor.answer(frames[0])
    except TelegramBadRequest:
        bubble = None

    # Открытие крутим в фоне, пока проигрываем кадры саспенса.
    open_task = asyncio.create_task(_do_open_and_render(session, user_id, code))

    if bubble is not None:
        for frame in frames[1:]:
            await asyncio.sleep(delay)
            try:
                await bubble.edit_text(frame)
            except TelegramBadRequest:
                break  # нечего/нельзя редактировать — просто ждём результат
    # Гарантируем минимальный саспенс, даже если кадры не отрисовались.
    elif frames:
        await asyncio.sleep(min(delay * len(frames), 1.5))

    text, markup, _ = await open_task

    if bubble is not None:
        try:
            await bubble.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass  # фолбэк ниже
    await anchor.answer(text, reply_markup=markup)



@router.message(RuCommand("открыть", "open"))
async def cmd_open(message: Message, session: AsyncSession, command_args: str) -> None:
    """Site-first (Release 2.2): открытие кейсов перенесено на сайт.

    Команда больше не вскрывает кейс внутри Telegram — ведёт на страницу кейсов.
    """
    if message.from_user is None:
        return
    url = f"{get_settings().website_url}/cases"
    await message.answer(
        texts.CASES_SITE_CARD,
        reply_markup=open_on_site(texts.CASES_SITE_BTN, url),
    )



@router.callback_query(F.data.startswith("case:open:"))
async def cb_case_open(callback: CallbackQuery, session: AsyncSession) -> None:
    """Site-first (Release 2.2): открытие кейсов перенесено на сайт.

    Кнопка `case:open` больше не вешается на новые сообщения, но старые
    сообщения в чате могут её ещё содержать. Чтобы НИГДЕ не осталось сценария
    открытия внутри Telegram, при нажатии ведём игрока на сайт, а не вскрываем
    кейс. Открытие (`_open_with_animation`/`open_case`) больше не вызывается из
    бота — остаётся только как внутренний конвейер для сайта.
    """
    await callback.answer()
    if callback.message is not None:
        url = f"{get_settings().website_url}/cases"
        await callback.message.answer(
            texts.CASES_SITE_CARD,
            reply_markup=open_on_site(texts.CASES_SITE_BTN, url),
        )



def _parse_gift_action(data: str) -> tuple[str, int] | None:
    """Разбирает callback вида ``gift:<action>:<delivery_key>:<user_id>``.

    Возвращает (delivery_key, user_id) или None при некорректном формате.
    ``delivery_key`` может содержать двоеточия (формат ``casegift:<uid>:<hex>``),
    поэтому user_id берём как ПОСЛЕДНИЙ сегмент, а ключ — всё между action и ним.
    """
    parts = data.split(":")
    if len(parts) < 4:
        return None
    try:
        uid = int(parts[-1])
    except ValueError:
        return None
    key = ":".join(parts[2:-1])
    if not key:
        return None
    return key, uid


@router.callback_query(F.data.startswith("gift:keep:"))
async def cb_gift_keep(callback: CallbackQuery, session: AsyncSession) -> None:
    """«Оставить» выпавший подарок (P1/P7): подарок остаётся pending-доставкой.

    Ничего не меняем в БД — заявка уже создана при открытии. Просто убираем
    кнопки и подтверждаем выбор владельцу.
    """
    parsed = _parse_gift_action(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    _, owner_id = parsed
    if callback.from_user is None or callback.from_user.id != owner_id:
        await callback.answer(texts.GIFT_ACTION_NOT_YOURS, show_alert=False)
        return

    await callback.answer()
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(texts.GIFT_KEPT.format(gift="подарок"))


@router.callback_query(F.data.startswith("gift:sell:"))
async def cb_gift_sell(callback: CallbackQuery, session: AsyncSession) -> None:
    """«Продать» выпавший подарок за ешки (P5): мгновенная продажа за 70%.

    Защита: продать может только владелец приза (user_id из callback). Сама
    продажа атомарна и идемпотентна (блокировка строки доставки в sell_gift).
    """
    parsed = _parse_gift_action(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    delivery_key, owner_id = parsed
    if callback.from_user is None or callback.from_user.id != owner_id:
        await callback.answer(texts.GIFT_ACTION_NOT_YOURS, show_alert=False)
        return

    outcome = await sell_gift(
        session, idempotency_key=delivery_key, user_id=owner_id, channel="bot"
    )

    if outcome.status == "ok":
        # Имя из каталога, не внутренний код (релизное требование).
        names = await gifts_repo.get_names_by_codes(
            session, [outcome.gift_code or ""]
        )
        gift_name = names.get(outcome.gift_code or "") or "подарок"
        text = texts.GIFT_SOLD.format(
            gift=gift_name,
            amount=money(outcome.amount),
            balance=money(outcome.balance or 0),
        )

    elif outcome.status == "not_pending":
        text = texts.GIFT_SELL_NOT_PENDING
    elif outcome.status == "no_value":
        text = texts.GIFT_SELL_NO_VALUE
    else:
        text = texts.GIFT_SELL_NOT_FOUND

    await callback.answer()
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(text)


@router.callback_query(F.data.startswith("gift:withdraw:"))
async def cb_gift_withdraw(callback: CallbackQuery, session: AsyncSession) -> None:
    """«Вывести» выпавший подарок (P2/P6): попытка авто-выдачи через Telegram.

    Сценарий: игрок выбрал вывести подарок. Пытаемся выдать сразу
    (:func:`deliver_gift`, тот же конвейер, что у магазина). Успех — подарок
    отправлен, кнопки убираем. Временная неудача (нет Stars, ошибка API, выдача
    выключена) — подарок остаётся pending, показываем кнопку «Попробовать ещё
    раз» (P6). Постоянная неудача — доставка отменена с возвратом стоимости
    (логика внутри deliver_gift). Защита: действовать может только владелец.
    """
    parsed = _parse_gift_action(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    delivery_key, owner_id = parsed
    if callback.from_user is None or callback.from_user.id != owner_id:
        await callback.answer(texts.GIFT_ACTION_NOT_YOURS, show_alert=False)
        return

    # Фиксируем «намерение вывести» прежде внешнего вызова не нужно — deliver_gift
    # сам берёт доставку FOR UPDATE и идемпотентен (повторный клик безопасен).
    settings = get_settings()
    outcome = await deliver_gift(
        session,
        callback.bot,
        idempotency_key=delivery_key,
        enabled=settings.gifts_delivery_enabled,
        channel="bot",
    )

    await callback.answer()
    if callback.message is None:
        return

    if outcome.status == "completed":
        # Успех — подарок отправлен, кнопки больше не нужны.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(texts.GIFT_WITHDRAW_SENT.format(gift="подарок"))
    elif outcome.status == "skip":
        # Уже обработана (продали/выдали ранее) — повторно ничего не делаем.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(texts.GIFT_WITHDRAW_NOT_PENDING)
    elif outcome.status == "cancelled":
        # Постоянная ошибка: доставка отменена, стоимость возвращена внутри
        # deliver_gift. Кнопки убираем — повторять нечего.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(texts.GIFT_WITHDRAW_NOT_PENDING)
    else:
        # pending (временная неудача): оставляем подарок, даём кнопку повтора.
        retry = gift_retry(delivery_key, owner_id, retry_label=texts.CASE_GIFT_RETRY_BTN)
        try:
            await callback.message.edit_reply_markup(reply_markup=retry)
        except TelegramBadRequest:
            pass
        await callback.message.answer(texts.GIFT_WITHDRAW_PENDING.format(gift="подарок"))



