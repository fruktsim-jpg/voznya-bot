"""Хендлеры кейсов: /кейсы, /кейс, /открыть и кнопка открытия.

Открытие идёт ТОЛЬКО через :func:`app.features.cases.service.open_case` —
единственную атомарную точку выдачи. Хендлеры лишь валидируют ввод, проверяют
владельца кнопки и рендерят результат.
"""

from __future__ import annotations

from aiogram import F, Router

from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.filters import RuCommand
from app.core.keyboards import case_open
from app.core.money import money
from app.core.responses import notify_and_cleanup
from app.features.cases.events import CaseOpenEvent, emit_case_opened
from app.features.cases.service import OpenResult, open_case
from app.models import CaseReward, Inventory
from app.repositories import cases as cases_repo
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
    """Список доступных кейсов."""
    user = message.from_user
    if user is None:
        return

    cases = await cases_repo.get_active_cases(session)
    if not cases:
        await notify_and_cleanup(session, message, texts.CASES_EMPTY)
        return

    lines = [texts.CASES_HEADER]
    for case in cases:
        owned = await _owned_count(session, user.id, case.item_code)
        owned_str = texts.CASES_ROW_OWNED.format(count=owned) if owned else ""
        lines.append(
            texts.CASES_ROW.format(
                name=case.name, cost=_cost_label(case), owned=owned_str
            )
            + f"\n   <code>/кейс {case.item_code}</code>"
        )
    await message.answer("\n".join(lines))


def _format_chance(weight: int, total: int) -> str:
    """Форматирует шанс выпадения в проценты."""
    if total <= 0:
        return "—"
    pct = weight / total * 100
    return f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%"


def _reward_label(reward: CaseReward) -> str:
    """Подпись награды в дроп-листе (без раскрытия точных кодов)."""
    if reward.reward_kind == "currency":
        amount = reward.amount or 0
        return money(amount)
    if reward.reward_kind == "item":
        return reward.reward_item_code or "предмет"
    return reward.reward_kind


@router.message(RuCommand("кейс", "case"))
async def cmd_case(message: Message, session: AsyncSession, command_args: str) -> None:
    """Карточка одного кейса: описание, дроп-лист с шансами, кнопка открытия."""
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

    body = [
        texts.CASE_CARD_HEADER.format(
            name=case.name,
            description=case.description or "",
            cost=_cost_label(case),
        )
    ]
    for r in rewards:
        body.append(
            texts.CASE_CARD_ROW.format(
                label=_reward_label(r), chance=_format_chance(r.weight, total)
            )
        )
    owned = await _owned_count(session, user.id, case.item_code)
    body.append(texts.CASE_CARD_FOOTER.format(count=owned))

    await message.answer(
        "\n".join(body), reply_markup=case_open(case.item_code, user.id)
    )


def _render_open(result: OpenResult) -> str:
    """Рендерит результат успешного открытия."""
    if result.reward_kind == "currency":
        line = texts.CASE_OPEN_WIN_CURRENCY.format(
            case=result.case_name,
            amount=money(result.amount or 0),
            balance=money(result.balance or 0),
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
            item=result.reward_item_name or result.reward_item_code or "предмет",
            qty=qty,
        )

    if result.is_jackpot:
        return texts.CASE_OPEN_JACKPOT.format(line=line)
    return line


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
) -> tuple[str, OpenResult]:
    """Открывает кейс и возвращает (текст, результат) для рендера."""
    result = await open_case(session, user_id=user_id, case_item_code=code)
    failure = _render_failure(result)
    if failure is not None:
        return failure, result

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
    return _render_open(result), result


@router.message(RuCommand("открыть", "open"))
async def cmd_open(message: Message, session: AsyncSession, command_args: str) -> None:
    """Открыть кейс командой /открыть код."""
    user = message.from_user
    if user is None:
        return

    code = command_args.split()[0] if command_args else ""
    if not code:
        await notify_and_cleanup(session, message, texts.CASE_OPEN_USAGE)
        return

    text, _ = await _do_open_and_render(session, user.id, code)
    await message.answer(text)


@router.callback_query(F.data.startswith("case:open:"))
async def cb_case_open(callback: CallbackQuery, session: AsyncSession) -> None:
    """Открытие кейса по кнопке.

    Защита от чужих нажатий: callback несёт user_id адресата. Защита от двойного
    клика/гонок — внутри open_case (блокировки строк), здесь только UX.
    """
    data = callback.data or ""
    parts = data.split(":")
    # case:open:<item_code>:<user_id>
    if len(parts) != 4:
        await callback.answer()
        return
    code = parts[2]
    try:
        target_id = int(parts[3])
    except ValueError:
        await callback.answer()
        return

    if callback.from_user is None or callback.from_user.id != target_id:
        await callback.answer(texts.CASE_NOT_YOURS, show_alert=False)
        return

    text, _ = await _do_open_and_render(session, target_id, code)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(text)
