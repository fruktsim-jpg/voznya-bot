# Release 2.2 — Inventory, Shop, Auto Delivery

Превращаем предметы из одноразового события в полноценные игровые объекты:

```
Кейс / Магазин → Инвентарь → Продать / Вывести / Подарить → Авто-выдача
```

Сайт (`v0-voznya`) — основная платформа (P8). Бот — резерв, уведомления,
админ-инструмент и фолбэк ручной выдачи.

---

## Что уже было (Release 2.1) и переиспользуется

Серверная экономика подарков целиком собрана и покрыта тестами в боте:

- `app/features/gifts/service.py`: `buy_gift`, `deliver_gift` (авто-выдача +
  pending/refund), `sell_gift` (P5, 70% от стоимости), `refund_gift`,
  `complete_gift_manually` (ручная выдача админом).
- `app/features/cases/service.py`: `open_case` кладёт приз tg_gift в pending
  `gift_transactions` (тот же конвейер, что магазин), отдаёт `delivery_key`,
  `reward_value`, `reward_sell_amount`.
- Экран выбора в боте после кейса: «Оставить / Продать» (`case_gift_choice`).
- Сайт: `lib/cases-open.ts` — точный TS-порт `open_case` против общей Postgres.

Release 2.2 закрывает недостающую **website-first** часть и добавляет на боте
«Вывести» (авто-выдача с повтором).

---

## Website (v0-voznya) — основная платформа (P0/P1/P2/P3/P4/P5/P8)

Сайт и бот не достучатся друг до друга (Vercel vs VPS), но делят одну Postgres.
Поэтому мутации на сайте — TS-порты ботовой логики против общей БД в одной
транзакции (как `lib/cases-open.ts`). Формулы 1:1 с ботом.

Новые файлы:

- `lib/economy-rules.ts` — `ESHKI_PER_STAR=10`, `ITEM_SELL_RATE=0.70` (зеркало
  `app/settings/balance.py`; менять синхронно).
- `lib/inventory-list.ts` (server-only) — читает инвентарь: стековые предметы
  (`inventory ⋈ inventory_items`) + pending Telegram Gifts/Premium
  (`gift_transactions`, status='pending'). Считает стоимость и сумму продажи.
- `lib/inventory-actions.ts` (server-only) — `sellInventoryItem` (порт
  `sell_gift`: FOR UPDATE, +70% через `transactions`, доставка → cancelled,
  освобождение резерва каталога для покупок) и `withdrawInventoryItem`
  (помечает `meta.withdraw_requested`; реальную выдачу делает бот).
- `app/api/inventory/route.ts` — GET свой инвентарь (owner из подписанной
  сессии).
- `app/api/inventory/sell/route.ts` — POST продать (`{ deliveryKey }`).
- `app/api/inventory/withdraw/route.ts` — POST вывести (`{ deliveryKey }`).
- `app/inventory/page.tsx` + `components/v2/inventory-client.tsx` — раздел
  `/inventory` (P0/P2): Steam-style сетка, для подарков действия
  Оставить / Продать за N / Вывести; Premium с особым тиром и «Активировать».
- `lib/balance-events.ts` — мини pub/sub для мгновенного обновления баланса
  (P5).

Изменённые файлы:

- `components/v2/case-opener.tsx` (P1): после открытия для tg_gift показывается
  экран выбора «Оставить / Продать за N / Вывести»; после любого открытия и
  после продажи дёргается `notifyBalanceChanged()`.
- `components/auth/user-menu.tsx` (P5): подписка на `onBalanceChanged` →
  refetch `/api/me/summary`; чип баланса в шапке обновляется без F5. Пункт меню
  «Инвентарь» теперь ведёт на `/inventory`, «Подарки» → «🛒 Магазин».
- `components/v2/bottom-nav.tsx` (P4): добавлен «Инвентарь», «Подарки»
  переименован в «Магазин».

Owner всегда берётся из подписанной сессии (`session.uid`), не из тела запроса —
продать/вывести можно только свой предмет.

### Целевой цикл на сайте

```
Кейс (/api/cases/open) → подарок в /inventory (pending gift)
   → Продать (/api/inventory/sell)  → +ешки, доставка cancelled, баланс обновлён
   → Вывести (/api/inventory/withdraw) → pending помечен к выдаче → бот выдаёт
Магазин → тот же инвентарь → те же действия
```

---

## Бот — фолбэк «Вывести» с повтором (P2/P6)

К экрану выбора после кейса добавлена третья кнопка **«📤 Вывести»**:

- `app/core/keyboards.py`: `case_gift_choice(..., withdraw_label=...)` добавляет
  кнопку `gift:withdraw`; новая `gift_retry(...)` — кнопка «🔁 Попробовать выдать
  ещё раз» (тот же `gift:withdraw`).
- `app/features/cases/handlers.py`: хендлер `cb_gift_withdraw` зовёт
  `deliver_gift`:
  - `completed` → «отправлен», кнопки убираем;
  - `pending` (временная ошибка: нет Stars / API / выдача выключена) → подарок
    остаётся, показываем кнопку повтора (P6);
  - `cancelled` (постоянная ошибка) → доставка отменена с возвратом стоимости
    внутри `deliver_gift`.
- Идемпотентность: `deliver_gift` берёт доставку FOR UPDATE — двойной клик и
  повтор безопасны.

Админ-инструменты P6 уже есть: `/gifts_pending`, `/gifts_done` (повтор/ручная
выдача), `/gifts_refund`.

---

## P3 — Premium как объект

Premium живёт как обычный pending-подарок в инвентаре (код содержит `premium`):

- на сайте — отдельный тир (мифический) и действие «Активировать» (= withdraw,
  заявка боту); продажа доступна как у любого предмета;
- на боте — тот же pending → ручная выдача `/gifts_done` (Premium через
  `sendGift` не выдаётся — см. P7/Release 2.1).

Архитектура заложена: подарок другому человеку — отдельная задача (создание
адресной задачи выдачи поверх существующего `gift_transactions`).

---

## P7 — Ревизия Telegram-интеграции (практические выводы)

`requirements.txt` уже на `aiogram>=3.28,<3.29`, авто-выдача проверена на проде.
Практические изменения этого релиза:

- Авто-выдача стала доступной игроку напрямую через «Вывести» (раньше — только
  после покупки магазина или вручную админом).
- Pending-логика НЕ избыточна и остаётся: это штатный фолбэк (нет Stars,
  Premium, выдача выключена флагом), а не костыль эпохи 3.13.
- `getattr`-защиты в `app/services/telegram_gifts.py` (`send_gift`,
  `get_my_star_balance`, `get_available_gifts`) безвредны; удалять стоит только
  после жёсткого пина версии — оставлены как дешёвая защита.
- Реальная авто-выдача включается флагом `GIFTS_DELIVERY_ENABLED=true` + Stars
  на балансе бота + `telegram_gift_id` у позиций каталога. Без флага «Вывести»
  корректно оставляет подарок в pending (деньги/стоимость не теряются).

---

## Проверка

- Сайт: `tsc --noEmit` — новые файлы компилируются без ошибок (две
  пред-существующие ошибки в `lib/casino.ts` и `lib/economy-analytics.ts` к
  этому релизу не относятся).
- Бот: добавлен `tests/test_gift_withdraw_keyboard.py` (чистые проверки
  клавиатур). Полный прогон — в Docker/CI:

  ```
  docker compose exec bot pytest tests -q
  ```

  Локально на машине разработчика нет Python/запущенного Docker, поэтому
  рантайм-тесты бота прогоняются в контейнере (как и раньше).
