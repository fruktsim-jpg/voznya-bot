# Release 2.2 — End-to-end аудит экономики подарков

Аудит по факту кода (не по догадкам). Источники:
- бот: `app/features/gifts/service.py`, `app/features/cases/rewards.py`,
  `app/features/gifts/worker.py`, `app/web/internal_api.py`,
  `app/repositories/gifts.py`;
- сайт: `lib/inventory-actions.ts`, `lib/shop-actions.ts`,
  `app/api/inventory/*`, `lib/bot-client.ts`.

Курс: 1★ = 10 ешек. Продажа = `floor(full_value × 0.70)`. `full_value` =
`price_eshki` (фолбэк `star_cost×10`).

Ключевое различие, проходящее через всё:
**покупка магазина** имеет денежную проводку `transaction_id` и держит
`reserved` в каталоге; **приз кейса** — `transaction_id IS NULL`, резерв не
занимает. Это `_is_shop_purchase()` (бот) / `isShopPurchase()` (сайт).

---

## 1. Таблица сценариев

| # | Сценарий | Δ баланс | На сколько | Δ статус доставки | Предмет | Запись в леджер | reason |
|---|----------|----------|------------|-------------------|---------|-----------------|--------|
| 1 | Покупка в магазине | −цена | −price_eshki | (нет→) pending | появляется (pending) | transactions + purchase_history + gift_transactions | `purchase` |
| 2 | Выпадение из кейса (tg_gift) | 0 | — | (нет→) pending | появляется (pending) | gift_transactions (без денег); открытие в case_openings | — |
| 2c| Выпадение из кейса (currency) | +amount | +amount | — | — | transactions | `reward` |
| 2i| Выпадение из кейса (item) | 0 | — | — | стек +qty | inventory_history | — |
| 3 | Продажа (sell) | +продажа | +floor(fv×0.7) | pending→cancelled | исчезает | transactions (reward) + meta.sold | `reward` |
| 4 | Вывод себе (auto, успех) | 0 | — | pending→completed | исчезает (выдан в TG) | stars ledger (gift_send, если star_cost>0); shop: reserved−1, sold_count+1 | — |
| 5 | Отправка другу @username (успех) | 0 | — | pending→completed | исчезает; meta.gifted_to | как #4 + meta.gifted_to | — |
| 5q| Отправка другу (бот недоступен) | 0 | — | pending (без изм.) | остаётся; meta.gift_to+withdraw_requested | — | — |
| 5l| Отправка по ссылке (claim) | 0 | — | pending→completed при клейме | исчезает у дарителя | как #4 | — |
| 6 | Передача игроку Возни | 0 | — | pending (recipient сменился) | переходит к новому владельцу | meta.transferred_* | — |
| 7 | Авто-выдача воркером (успех) | 0 | — | pending→completed | исчезает | как #4 | — |
| 8 | Возврат после ошибки (shop) | +цена | +price_eshki | pending→cancelled | исчезает | transactions (reward) + reserved−1 | `reward` |
| 8c| Возврат после ошибки (приз кейса) | +внутр. стоимость | +star_cost×10 | pending→cancelled | исчезает | transactions (reward), пул НЕ трогаем | `reward` |
| 9 | Pending (промежуточный) | 0 | — | остаётся pending | остаётся | meta.attempts/last_error | — |
| 10| Completed | как #4 | — | terminal | выдан | — | — |
| 11| Cancelled | уже учтён в #3/#8 | — | terminal | израсходован | — | — |

Замечания по балансу:
- **Продать дороже покупки нельзя**: продажа = 70% от той же `price_eshki`, что
  списалась при покупке. Купил за 1050 → продал за 735 (−315). Сток ешек, не дюп.
- **Возврат покупки = полная цена** (#8): купил за 1050, выдать не смогли →
  вернули 1050. Чистый ноль. Корректно.
- **Возврат приза кейса** (#8c) компенсирует `star_cost×10` (внутреннюю
  стоимость), т.к. игрок за приз не платил — это эмиссия, помечена
  `source=case_prize_refund`.

---

## 2. Аудит дюпов

Базовая защита везде одна: **строка `gift_transactions` берётся `FOR UPDATE`**,
все переходы делаются только из статуса `pending` и переводят в терминальный
(`completed`/`cancelled`). Любой второй проход видит уже не-`pending` → `skip`.

| Вектор | Защита | Вердикт |
|--------|--------|---------|
| Двойная выдача (повторный клик) | `deliver_gift`: FOR UPDATE + `status != 'pending' → skip`. Второй клик ничего не делает | безопасно |
| Двойная продажа | `sell_gift`/`sellInventoryItem`: FOR UPDATE + проверка pending. Второй → `not_pending` | безопасно |
| Двойной возврат | `refund_gift`/`_refund`: только из pending → cancelled. Повторно → skip | безопасно |
| Двойное начисление ешек (продажа) | начисление и `status=cancelled` в ОДНОЙ транзакции под локом. Откат статуса = откат денег | безопасно |
| Двойной Premium | Premium = тот же gift_transactions + FOR UPDATE. Выдаётся вручную (`complete_gift_manually`), pending→completed один раз | безопасно |
| Сайт + бот одновременно | обе стороны лочат ОДНУ строку в ОДНОЙ Postgres (`withTransaction`/`session FOR UPDATE`). Сериализуются | безопасно |
| Воркер + ручная выдача | оба идут через `deliver_gift`/`complete_gift_manually` с FOR UPDATE; кто первый перевёл в completed — второй skip | безопасно |
| Авто-выдача (сайт→бот) + воркер | сайт зовёт `/internal/gifts/deliver` (тот же `deliver_gift` + FOR UPDATE); воркер берёт ту же строку. Сериализуются | безопасно |
| Withdraw API: прямой вызов + флаг | если бот вернул `pending`, API дополнительно ставит `withdraw_requested` — это лишь meta-флаг, не выдача. Реальную выдачу делает только `deliver_gift` под локом | безопасно |

Дополнительно: `idempotency_key` UNIQUE на `gift_transactions` — повторное
СОЗДАНИЕ доставки с тем же ключом невозможно (покупка/приз генерят `secrets`).

**Дюпов не найдено.** Инвариант держится на: (1) единая БД, (2) FOR UPDATE на
строке доставки, (3) единственный разрешённый переход из `pending`.

---

## 3. Источники правды (кто пишет / кто читает)

| Сущность | Хранилище | Пишет | Читает |
|----------|-----------|-------|--------|
| Баланс | `users.balance` + `transactions` (леджер) | бот: `change_balance`/`change_balance_tx`; сайт: прямой UPDATE в `sellInventoryItem`/`shop-actions` (reason='reward'/'purchase') — **тот же леджер** | бот (хендлеры), сайт (профиль/инвентарь/шапка) |
| Подарки (каталог) | `gift_catalog` | миграции-сиды (0018–0030); админка сайта (`/api/admin/gifts`) | бот (`gifts_repo`), сайт (`lib/gifts.ts`, инвентарь, casino-feed) |
| Premium | `gift_catalog` (позиции premium_*) + `gift_transactions` | как обычный подарок; выдача — `complete_gift_manually` (вручную) | бот, сайт (инвентарь, isPremium) |
| Инвентарь | `inventory`(+`inventory_items`) и `gift_transactions`(pending) | бот: `grant_item`, `buy_gift`, `_grant_tg_gift`; сайт: `inventory-actions` (sell/transfer) | бот (`/инвентарь`), сайт (`lib/inventory-list.ts`) |
| Доставки | `gift_transactions` | бот: `service.py` (buy/deliver/sell/refund/manual); сайт: `inventory-actions.ts` (sell/withdraw-флаг/transfer/claim) | бот (worker, /gifts_pending), сайт (инвентарь, админка deliveries) |
| Stars (расход) | stars-леджер (`stars_service.record_out`) | только бот (`deliver_gift`, при успехе и star_cost>0) | бот, аналитика |

### Где ДВЕ реализации одной логики (риск рассинхрона)
Это не дюп-баги, но точки, требующие синхронного изменения:
1. **`sell_gift` (Python) ↔ `sellInventoryItem` (TS)** — продажа реализована
   дважды. Обе: FOR UPDATE, reason='reward', 70%, reserved−1 для shop. Формулы
   совпадают (`ITEM_SELL_RATE`, `ESHKI_PER_STAR` продублированы константами).
   ⚠️ Любое изменение ставки/формулы надо менять В ОБОИХ местах.
2. **`_item_full_value` (Python) ↔ `itemFullValue` (TS, в двух файлах:
   inventory-list и inventory-actions)** — тройное дублирование формулы
   ценности. Совпадает, но хрупко.
3. **Вывод**: сайт НЕ дублирует выдачу — он зовёт бота (`/internal/gifts/deliver`).
   Это правильно: реальная выдача в Telegram — единственная реализация
   (`deliver_gift`). Сайтовый `withdrawInventoryItem` ставит только meta-флаг.

Рекомендация (на будущее, не блокер релиза): вынести ставку/формулу в один
разделяемый источник или покрыть «golden»-тестом по обе стороны, чтобы
расхождение ловилось в CI.

---

## 4. Кейсы после всех изменений

| Путь | Поведение | Статус |
|------|-----------|--------|
| Кейс → подарок (оседает в инвентаре) | `_grant_tg_gift` создаёт pending gift_transactions (transaction_id=NULL). Имя в экранах — из каталога (фикс P0, кодов не видно) | ок |
| Кейс → продажа | `sell_gift`: приз кейса, пул НЕ трогаем, начисление 70% от full_value, reason='reward'. Имя из каталога (фикс) | ок |
| Кейс → вывод | `deliver_gift`: успех → completed, пул НЕ трогаем (приз не резервировал), Stars-расход в леджер | ок |
| Кейс → подарок другу | `deliver_gift(recipient_override)` или очередь (`gift_to`). Возврат при ошибке — плательщику (исходному игроку) | ок |
| Кейс → Premium | `_grant_tg_gift` для premium_*; выдача вручную `complete_gift_manually`, пул не трогаем | ок |
| Кейс → возврат приза | `_refund` ветка не-shop: компенсация star_cost×10, `case_prize_refund` | ок |

Ничего не разъехалось: все кейсовые призы идут ОБЩИМ конвейером Gifts
(`gift_transactions` + `deliver_gift`/`sell_gift`/`_refund`), отдельной системы
выдачи нет. Признак `_is_shop_purchase` корректно разводит экономику пула и
возвратов между покупками и призами.

---

## Итог

- **Таблица сценариев** (раздел 1): баланс/статус/предмет/леджер/reason по всем
  11 состояниям — задокументированы по факту кода.
- **Дюпы** (раздел 2): не найдено. Инвариант — единая БД + FOR UPDATE + единственный
  переход из pending. Все опасные комбинации (сайт+бот, воркер+ручная,
  авто+админ, повторный клик) сериализуются на строке доставки.
- **Источники правды** (раздел 3): по каждой сущности один пишущий контур
  (леджер/строка), сайт пишет в ТЕ ЖЕ таблицы. Выявлено дублирование ФОРМУЛ
  (продажа/ценность) Python↔TS — не баг, но требует синхронного изменения;
  рекомендация — общий тест.
- **Кейсы** (раздел 4): все пять путей целы после правок имён/лимиток.

Фундамент экономики и инвентаря — консистентный. Единственный технический долг —
дублирование формул продажи/ценности между ботом и сайтом (осознанный порт ради
изоляции Vercel↔VPS), его стоит закрыть golden-тестом, но это не блокер.
