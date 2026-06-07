"""Логика открытия кейса — ЕДИНСТВЕННАЯ точка выдачи наград из кейсов.

Открытие полностью атомарно: всё (списание стоимости, выпадение, выдача награды,
инкремент лимита, запись в леджер открытий) происходит в одной транзакции
сессии. Commit/rollback делает DbSessionMiddleware: при успешном возврате из
хендлера сессия коммитится, при исключении — откатывается.

ВАЖНО про control-flow: middleware коммитит при ОБЫЧНОМ возврате. Поэтому
нельзя сначала списать ресурс, а потом вернуть «неуспех» — это закоммитит
частичную мутацию (например, съест ключ, не выдав награду). Решение —
пред-проверка (pre-flight): СНАЧАЛА под блокировками строк проверяем, что всех
ресурсов хватает, и только потом, когда отказ уже невозможен, выполняем сами
мутации. Неуспешные исходы возвращаются ДО первой записи.

Защита от двойного открытия:
* строка владения предметом-ключом блокируется ``FOR UPDATE`` в pre-flight и
  держится до конца транзакции — двойной клик/два callback сериализуются и не
  спишут один кейс дважды;
* строка пользователя блокируется ``FOR UPDATE`` (баланс не уйдёт в минус при
  гонке);
* строки дроп-листа берутся ``FOR UPDATE`` (лимитные награды не уйдут в минус).

Выпадение воспроизводимо: в ``case_openings`` сохраняется ``roll`` и
``weight_snapshot`` (слепок весов на момент открытия).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.economy_events import EVENT_PURCHASE
from app.features.cases.rewards import RewardResult, grant_reward
from app.models import CaseOpening, CaseReward, Inventory, InventoryItem, User

# Виды наград, допустимые в горячем пути открытия. tg_gift включён: реальные
# Telegram Gifts/Premium выдаются НЕ синхронно, а через pending-доставку
# (см. rewards._grant_tg_gift) — тем же конвейером, что магазин подарков.
REWARD_KINDS_OPENABLE = ("item", "currency", "tg_gift")


from app.repositories import cases as cases_repo
from app.services.economy import change_balance_tx
from app.services.inventory_grant import consume_item



@dataclass(frozen=True)
class OpenResult:
    """Итог открытия кейса для рендера в хендлере."""

    status: str  # "ok" | "not_found" | "inactive" | "no_key" | "not_enough" | "empty" | "error"
    case_name: str = ""
    reward_kind: str = ""
    reward_item_code: str | None = None
    reward_item_name: str | None = None
    reward_rarity: str | None = None
    amount: int | None = None
    qty: int = 1
    is_jackpot: bool = False
    balance: int | None = None
    # Для tg_gift — idempotency_key созданной pending-доставки (уведомления).
    delivery_key: str | None = None
    error: str | None = None



def _pick_reward(rewards: list[CaseReward]) -> tuple[CaseReward, int, int]:
    """Взвешенный выбор награды. Возвращает (награда, roll, сумма весов).

    ``roll`` — целое в [0, total_weight); выбор — по накопительной сумме весов.
    Используется ``secrets`` (CSPRNG) — не предсказуемо игроком.
    """
    total = sum(r.weight for r in rewards)
    roll = secrets.randbelow(total)
    acc = 0
    for r in rewards:
        acc += r.weight
        if roll < acc:
            return r, roll, total
    # Теоретически недостижимо (roll < total): подстраховка — последняя строка.
    return rewards[-1], roll, total


async def open_case(
    session: AsyncSession, *, user_id: int, case_item_code: str
) -> OpenResult:
    """Открывает кейс игроком ``user_id``. Полностью атомарно.

    Порядок (всё в одной транзакции):
      1) загрузить и проверить кейс (активность, окно дат);
      2) PRE-FLIGHT: под блокировками строк проверить, что хватает ключа и/или
         ешек — БЕЗ мутаций. Любой недостаток → ранний возврат (мутаций ещё нет,
         коммит middleware безвреден);
      3) выбрать награду по весам среди доступных (под блокировкой дроп-листа).
         Если доступных наград нет — исключение (rollback, ничего ещё не списано
         тоже, но это аварийный конфиг кейса);
      4) выполнить мутации, отказ которых уже невозможен: списать ключ, списать
         ешки, инкрементировать лимит, выдать награду, записать леджер.
    Любая ошибка → rollback всего (middleware).
    """
    case = await cases_repo.get_case_by_item_code(session, case_item_code)
    if case is None:
        return OpenResult(status="not_found")

    now = datetime.now(timezone.utc)
    if not case.is_active:
        return OpenResult(status="inactive", case_name=case.name)
    if case.starts_at is not None and case.starts_at > now:
        return OpenResult(status="inactive", case_name=case.name)
    if case.ends_at is not None and case.ends_at < now:
        return OpenResult(status="inactive", case_name=case.name)

    needs_currency = (
        case.open_cost_kind == "currency" and case.open_cost_amount > 0
    )

    # --- 2. PRE-FLIGHT: проверка ресурсов под блокировками, БЕЗ мутаций ------
    # Сначала убеждаемся, что списание ВОЗМОЖНО для всех видов стоимости, и
    # только потом начинаем списывать. Иначе ранний возврат после частичного
    # списания закоммитил бы потерю ресурса (middleware коммитит при возврате).
    # Блокировки, взятые здесь, держатся до конца транзакции — значения не
    # изменятся, поэтому фактические списания ниже уже не могут отказать.
    if case.consumes_key:
        owned = await session.scalar(
            select(Inventory.quantity)
            .where(Inventory.user_id == user_id)
            .where(Inventory.item_code == case.item_code)
            .with_for_update()
        )
        if not owned or owned < 1:
            return OpenResult(status="no_key", case_name=case.name)

    if needs_currency:
        user = await session.get(User, user_id, with_for_update=True)
        if user is None or user.balance < case.open_cost_amount:
            return OpenResult(status="not_enough", case_name=case.name)

    # --- 3. Выбор награды (под блокировкой строк дроп-листа) ----------------
    rewards = await cases_repo.get_available_rewards_for_update(
        session, case_item_code
    )
    # Валидатор скоупа V1: в горячем пути допускаем только item/currency.
    rewards = [r for r in rewards if r.reward_kind in REWARD_KINDS_V1]
    if not rewards:
        # Нет доступных наград (пустой дроп-лист / лимиты исчерпаны). Поднимаем
        # исключение → rollback (ничего ещё не списано, но кейс мисконфигурён).
        raise RuntimeError(f"case '{case_item_code}' has no available rewards")

    reward, roll, total_weight = _pick_reward(rewards)
    qty = (
        reward.min_qty
        if reward.min_qty == reward.max_qty
        else reward.min_qty + secrets.randbelow(reward.max_qty - reward.min_qty + 1)
    )

    # --- 4. Мутации (отказ уже невозможен — ресурсы заблокированы выше) ------
    # 4a. Списать предмет-ключ. consume_item повторно берёт FOR UPDATE на уже
    # удерживаемую строку (своя блокировка — безопасно) и гарантированно успеет.
    if case.consumes_key:
        ok = await consume_item(
            session,
            user_id=user_id,
            item_code=case.item_code,
            quantity=1,
            source="case",
            event="use",
            meta={"reason": "open_case"},
        )
        if not ok:
            # Недостижимо после pre-flight под блокировкой; защита от регрессий.
            raise RuntimeError(
                f"key consume failed after pre-flight for case '{case_item_code}'"
            )

    # 4b. Списать ешки за открытие (через экономическое ядро). Сохраняем id
    # проводки, чтобы связать открытие с его списанием (case_openings.
    # transaction_id) — точный per-open burn и сверка по леджеру.
    open_transaction_id: int | None = None
    if needs_currency:
        tx = await change_balance_tx(
            session,
            user_id,
            -case.open_cost_amount,
            reason=EVENT_PURCHASE,
            meta={"source": "case_open", "case": case.item_code},
        )
        open_transaction_id = tx.id


    # 4c. Инкремент лимита выпадения (для лимиток), безопасно при гонках.
    if reward.max_global_supply is not None:
        await session.execute(
            update(CaseReward)
            .where(CaseReward.id == reward.id)
            .values(granted_count=CaseReward.granted_count + 1)
        )

    # 4d. Выдача награды через единую точку.
    result: RewardResult = await grant_reward(
        session,
        user_id=user_id,
        reward_kind=reward.reward_kind,
        reward_item_code=reward.reward_item_code,
        amount=reward.amount,
        qty=qty,
        source="case",
        transaction_meta={"case": case.item_code, "reward_id": reward.id},
    )

    # 4e. Леджер открытия (честность + воспроизводимость).
    snapshot = [{"reward_id": r.id, "weight": r.weight} for r in rewards]
    session.add(
        CaseOpening(
            user_id=user_id,
            case_item_code=case.item_code,
            reward_id=reward.id,
            reward_kind=reward.reward_kind,
            reward_item_code=result.reward_item_code,
            amount=result.amount,
            qty=result.qty,
            roll=roll,
            weight_snapshot={"total": total_weight, "rewards": snapshot},
            transaction_id=open_transaction_id,
        )
    )


    # Имя/редкость предмета-награды для красивого ответа (best-effort).
    reward_item_name = None
    reward_rarity = None
    if result.reward_kind == "item" and result.reward_item_code:
        item = (
            await session.execute(
                select(InventoryItem.name, InventoryItem.rarity).where(
                    InventoryItem.code == result.reward_item_code
                )
            )
        ).first()
        if item is not None:
            reward_item_name = item[0]
            reward_rarity = item[1]

    return OpenResult(
        status="ok",
        case_name=case.name,
        reward_kind=result.reward_kind,
        reward_item_code=result.reward_item_code,
        reward_item_name=reward_item_name,
        reward_rarity=reward_rarity,
        amount=result.amount,
        qty=result.qty,
        is_jackpot=reward.is_jackpot,
        balance=result.new_balance,
    )
