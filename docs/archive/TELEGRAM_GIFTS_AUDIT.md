# Telegram Gifts — аудит реального API + точки логирования

Проверено по официальной документации Bot API (`https://core.telegram.org/bots/api`,
загружена и разобрана 2026-06-07). Ничего не предполагалось — только
подтверждённые методы и поля. Реализация проектируется вокруг этих ограничений.

---

## 1. Что Telegram РЕАЛЬНО предоставляет (подтверждено)

### Методы (существуют в Bot API)
| Метод | Параметры | Возвращает |
|---|---|---|
| `getAvailableGifts` | нет | `Gifts` (список `Gift`) — какие подарки бот может слать |
| `sendGift` | `user_id` ИЛИ `chat_id`, **`gift_id` (String, обяз.)**, `pay_for_upgrade?`, `text?`, `text_parse_mode?`, `text_entities?` | **`True`** на успехе |
| `getMyStarBalance` | нет | `StarAmount` — баланс Stars у бота |
| `getStarTransactions` | `offset?`, `limit?` (1–100) | `StarTransactions` — история Star-операций бота |
| `refundStarPayment` | `user_id`, `telegram_payment_charge_id` | `True` — возврат входящего Star-платежа |

### Объект `Gift` (из getAvailableGifts)
`id` (String), `sticker`, **`star_count`** (Stars за отправку), `upgrade_star_count?`,
`is_premium?`, `has_colors?`, `total_count?`, **`remaining_count?`** (остаток для
лимитных), `personal_total_count?`, **`personal_remaining_count?`** (сколько ещё
может отправить именно этот бот).

---

## 2. Ключевые ограничения (определяют дизайн)

1. **`sendGift` возвращает только `True` — НЕТ charge_id / id отправки.**
   Telegram не даёт идентификатора конкретной выдачи. Значит:
   - «telegram_charge_id для выдачи» из прошлого плана **не существует** — убираем;
   - единственный внешний след расхода — `getStarTransactions` (история Stars
     бота) и `getMyStarBalance` (баланс до/после). Сверять расход надо по ним;
   - **идемпотентность — целиком на нашей стороне** (Telegram её не обеспечивает).
2. **Боту нужны Stars на собственном балансе.** `sendGift` списывает Stars с
   баланса бота. Нет баланса → отправка не пройдёт. `getMyStarBalance` —
   обязательная пред-проверка перед выдачей.
3. **`gift_id` — это id из `getAvailableGifts`, не произвольный.** Наш
   `gift_catalog.telegram_gift_id` обязан хранить РЕАЛЬНЫЙ id из getAvailableGifts.
   Список подарков и их `star_count` задаёт Telegram, не мы (мы лишь назначаем цену
   в ешках). Подарки могут пропадать/меняться → каталог надо синхронизировать.
4. **Подарок может закончиться.** Лимитные подарки: `remaining_count` /
   `personal_remaining_count` падает до 0 → `sendGift` начнёт отвечать ошибкой.
   Наш `stock` — внутренний лимит, но реальный потолок задаёт ещё и Telegram.
5. **Получатель — `user_id`.** Для V1 (покупка себе) шлём на `user_id` покупателя.
   Подарок нельзя сконвертировать обратно в Stars получателем (так и задумано).
6. **Нет «резерва» на стороне Telegram.** Резерв (`gift_catalog.reserved`) — чисто
   наш учётный механизм; Telegram места не бронирует.

---

## 3. Как это меняет дизайн Gifts Shop V1

Относительно `GIFTS_SHOP_V1_PLAN.md` (поправки под реальный API):

- **Себестоимость = `Gift.star_count` от Telegram**, не наша оценка. При синке
  каталога подтягиваем `star_count` в `gift_catalog.star_cost` (источник правды —
  Telegram). Расхождение с нашим значением — сигнал обновить цену в ешках.
- **Выдача = `sendGift(user_id, gift_id)`** где `gift_id =
  gift_catalog.telegram_gift_id`. Перед вызовом — `getMyStarBalance` ≥
  `star_count` (иначе не пытаемся, оставляем `pending` или отменяем с возвратом).
- **Подтверждение успеха = ответ `True`.** Нет charge_id → в
  `gift_transactions.meta` пишем `{api_ok:true, sent_at, star_cost,
  star_balance_before, star_balance_after}` (баланс до/после как косвенное
  доказательство расхода вместо charge_id).
- **Сверка расхода Stars** — периодически через `getStarTransactions` (отдельный
  reconcile-инструмент, не в горячем пути). В V1 расход Stars считаем по
  `completed`-доставкам (как и планировали).
- **Идемпотентность** — наш `idempotency_key` UNIQUE + `status` под `FOR UPDATE`
  (Telegram не помогает). Перед `sendGift` строка доставки берётся `FOR UPDATE`;
  если уже `completed` — не шлём.
- **getAvailableGifts-синк** — админ-кнопка «обновить из Telegram»: сверяет
  `telegram_gift_id`, `star_count`, доступность (`remaining_count`/
  `personal_remaining_count`), помечает исчезнувшие позиции неактивными.

### Карта ошибок Telegram → действие
| Ситуация | Наше действие |
|---|---|
| Нет Stars на балансе бота (`getMyStarBalance < star_count`) | НЕ слать; оставить `pending`, уведомить админа; не списывать повторно |
| Подарок закончился/недоступен (`remaining`/`personal_remaining`=0, ошибка sendGift) | `cancelled` + **возврат ешек** игроку + деактивировать позицию |
| `gift_id` неверный/устарел | `cancelled` + возврат ешек + пометка позиции на ресинк |
| Сетевая/временная ошибка, таймаут | оставить `pending`, ретрай (ограниченно, `meta.attempts`) — НЕ слать второй раз без FOR UPDATE-проверки |
| Неоднозначный ответ (нет явного True) | НЕ помечать `completed`; в ручной разбор (видно в экране доставок) |
| Получатель не может принять | `cancelled` + возврат ешек |

### Что происходит, если подарок закончился
- При покупке: pre-flight проверяет наш `stock`. Дополнительно перед выдачей —
  фактическую доступность у Telegram (через свежий getAvailableGifts/ошибку
  sendGift). Если закончился ПОСЛЕ оплаты → автоматическая отмена с возвратом ешек
  и деактивация позиции (игрок не теряет ешки).

### Данные, которые Telegram возвращает после успеха
- Только `True`. Никаких id/charge. Поэтому доказательная база выдачи у нас:
  `status='completed'`, `meta.api_ok`, `star_balance_before/after`,
  `sent_at`, плюс возможность сверки через `getStarTransactions`.

---

## 4. Подробное логирование Gifts/Stars с первого дня

Цель: через полгода полностью восстановить любую покупку, выдачу, возврат, ошибку
или движение Stars. Все поля ниже пишутся в существующие сущности (без новых
фундаментальных таблиц), бот — единственный писатель.

### Обязательный набор полей на каждую операцию
| Поле | Где хранится |
|---|---|
| `user_id` | `purchase_history.user_id`, `gift_transactions.recipient_user_id` |
| `gift_id` (наш code) | `purchase_history.item_code`, `gift_transactions.item_code`; offer_id = catalog id |
| `telegram_gift_id` | `gift_catalog.telegram_gift_id` (+ снапшот в `gift_transactions.meta`) |
| стоимость в ешках | `purchase_history.price` |
| себестоимость в Stars | `meta.star_cost` (snapshot) в purchase_history и gift_transactions |
| статус операции | `gift_transactions.status` (pending/completed/cancelled) |
| время | `*.created_at` + `meta.sent_at` при выдаче |
| `transaction_id` | списание/возврат ешек — в purchase_history и gift_transactions |
| `idempotency_key` | `gift_transactions.idempotency_key` (UNIQUE) |
| причина отказа | `gift_transactions.meta.error` (+ status='cancelled') |
| данные Telegram | `meta.api_ok`, `meta.star_balance_before/after` (charge_id нет) |
| источник операции | `meta.channel` ∈ `bot`/`site`/`miniapp` |

Дополнительно для Stars-расхода: каждая `completed`-выдача несёт `star_cost` →
агрегат расхода. Доход (донат) появится с `stars_ledger` (отложено).

---

## 5. Аудит будущей интеграции — где заложить логирование СЕЙЧАС

Чтобы потом ничего не переделывать, фиксируем сквозные ключи `meta` и точки.

| Будущая система | Источник правды | Что писать сейчас / заложить |
|---|---|---|
| **Донат Stars → ешки** | `stars_ledger` (отложен, схема в ECONOMY_LOGGING_AUDIT §3.3) | при включении: `successful_payment` → строка с `telegram_payment_charge_id` (есть у входящих платежей!), user_id, stars, → начисление ешек через `change_balance_tx`. `meta.channel`. Невосстановимо без записи в момент платежа |
| **Покупка Gifts за ешки** | `transactions` + `purchase_history` | реализуем сейчас: `source='gift_buy'`, snapshot `star_cost`, `channel` |
| **Выдача Gifts** | `gift_transactions` | реализуем сейчас: `status`, `idempotency_key`, `api_ok`, `star_balance_*` |
| **Возврат Gifts** | `transactions` + `gift_transactions` | реализуем сейчас: `reason='reward'`, `meta.source='gift_refund'`, `of_transaction` |
| **Кейсы с Gifts** (reward_kind='tg_gift') | `case_openings` + `gift_transactions` | заложить: при выпадении Gift из кейса — создавать `gift_transactions(kind='tg_gift', gift_type='system', status='pending')` тем же путём выдачи, что и магазин. `case_openings.transaction_id` уже есть |
| **Кейсы со Stars** (reward_kind='stars') | `stars_ledger` | заложить ключ: выплата Stars игроку — расходная строка stars_ledger (когда появится). Помечать в `case_openings.meta` |
| **Магазин на сайте** | те же `transactions`/`purchase_history` | заложить `meta.channel='site'`; запись по-прежнему через бота (сайт только инициирует/читает) |
| **Mini App** | те же единые точки | заложить `meta.channel='miniapp'` во все экономические операции |

### Сквозные ключи `meta`, которые пишем с первого дня
- `source` — тип операции (`gift_buy`, `gift_refund`, `case_open`, `donate`…).
- `channel` — `bot` / `site` / `miniapp` (канал инициации). **Заложить везде сразу.**
- `star_cost` — себестоимость в Stars (snapshot) для всего, что связано с Gifts/Stars.
- `telegram_gift_id` — снапшот реального id Telegram на момент операции.
- для платежей (будущее) — `telegram_payment_charge_id` (есть у входящих, дедуп).

### Единый принцип
- Источник правды на актив: `transactions` (ешки), `stars_ledger` (Stars, позже).
- `gift_transactions` / `purchase_history` / `case_openings` — журналы-сателлиты
  со ссылкой `transaction_id`/`idempotency_key` на источник правды.
- Реальный Telegram Gift не кладётся в `inventory` (это внешний актив) — его жизнь
  целиком в `gift_transactions` (статус + meta).

---

## 6. Итог: что меняется в реализации против исходного плана
1. Себестоимость берём из `Gift.star_count` (Telegram — источник), не из догадки.
2. Перед выдачей — `getMyStarBalance` (боту нужны Stars).
3. Выдача = `sendGift(user_id, telegram_gift_id)`, успех = `True` (charge_id нет).
4. Доказательство выдачи = `status` + `star_balance_before/after` + сверка
   `getStarTransactions`, а не charge_id.
5. Идемпотентность полностью на нашей стороне (`idempotency_key` + FOR UPDATE).
6. Нужен синк каталога из `getAvailableGifts` (id/star_count/доступность).
7. `kind='tg_gift'` (решение пользователя) — отдельный тип актива.
