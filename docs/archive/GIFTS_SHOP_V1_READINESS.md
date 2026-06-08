# Gifts Shop V1 — аудит готовности + runbook эксплуатации

Проверено по официальной документации Bot API
(`https://core.telegram.org/bots/api`, загружена и разобрана 2026-06-07).
Без предположений — только подтверждённые методы/объекты/ограничения.

---

## 1. Какие методы Telegram используются

| Метод | Где в коде | Док (якорь на core.telegram.org/bots/api) | Возвращает |
|---|---|---|---|
| `sendGift` | `app/services/telegram_gifts.send_gift` → `bot.send_gift(user_id, gift_id, text)` | `#sendgift` | **`True`** |
| `getMyStarBalance` | `app/services/telegram_gifts.get_star_balance` → `bot.get_my_star_balance()` | `#getmystarbalance` | `StarAmount` |
| `getAvailableGifts` | пока НЕ вызывается (заложен под синк каталога) | `#getavailablegifts` | `Gifts` |

Ссылки на документацию (точные якоря):
- sendGift — https://core.telegram.org/bots/api#sendgift
- getAvailableGifts — https://core.telegram.org/bots/api#getavailablegifts
- getMyStarBalance — https://core.telegram.org/bots/api#getmystarbalance
- объект Gift — https://core.telegram.org/bots/api#gift
- объект Gifts — https://core.telegram.org/bots/api#gifts
- объект StarAmount — https://core.telegram.org/bots/api#staramount

## 2. Какие объекты возвращаются (подтверждённые поля)

- **`sendGift` → `True`.** Никакого id/charge_id отправки. Это единственное, что
  API даёт об успехе. (Поэтому доказательство выдачи у нас — `status` +
  `star_balance_before/after`, а не charge_id.)
- **`StarAmount`** = `amount` (Integer, может быть отриц.) + `nanostar_amount?`.
  Используем `amount`.
- **`Gift`** = `id` (String), `sticker`, `star_count` (Stars за отправку),
  `upgrade_star_count?`, `is_premium?`, `total_count?`, `remaining_count?`,
  `personal_total_count?`, `personal_remaining_count?`.
- **`Gifts`** = `gifts: Array of Gift`.

## 3. Ограничения (определяют дизайн)

1. `sendGift` возвращает только `True` — нет идентификатора выдачи →
   идемпотентность целиком наша (`idempotency_key` UNIQUE + FOR UPDATE).
2. Боту нужны **Telegram Stars на собственном балансе** — `sendGift` списывает
   Stars с баланса бота. Нет баланса → отправка не пройдёт.
3. `gift_id` — это реальный `id` из `getAvailableGifts`, не произвольная строка.
   В `gift_catalog.telegram_gift_id` обязан лежать настоящий id, иначе выдача
   завершится ошибкой → автоотмена с возвратом ешек.
4. Лимитные подарки заканчиваются (`remaining_count`/`personal_remaining_count`
   → 0) — внешний потолок поверх нашего `stock`.
5. Получатель — `user_id`; подарок получатель не может конвертировать в Stars.
6. Telegram не бронирует места — наш `reserved` чисто внутренний учёт.

## 4. Права и условия, нужные боту

- Действующий бот-токен (есть).
- Положительный **баланс Stars** у бота ≥ `star_cost` подарка (пополняется
  владельцем; проверяется через `getMyStarBalance` перед каждой выдачей).
- Версия Bot API/aiogram с методами Gifts. В репозитории `aiogram==3.13.1`:
  метод `get_my_star_balance` может отсутствовать — адаптер это переживает
  (вернёт «баланс неизвестен», выдачу не блокирует). `send_gift` при отсутствии
  бросит ошибку → доставка останется `pending` (ешки игрока в безопасности).
  Для реальной выдачи: при включении проверить версию aiogram и при
  необходимости обновить.
- Флаг `GIFTS_DELIVERY_ENABLED=true`.

## 5. Что готово vs архитектурная подготовка

**Готово и работает после деплоя (без Stars и без флага):**
- Каталог `gift_catalog` + админка `/admin/gifts` (CRUD, цены, stock).
- Публичная витрина сайта `/gifts` (цена в ешках, остаток).
- Витрина в боте `/подарки` + кнопки покупки.
- **Покупка за ешки** (атомарно): списание через экономическое ядро, `reserved+1`,
  `purchase_history(source='gift')`, `gift_transactions(status='pending')`.
- **Возврат** ешек (`refund_gift` / автоотмена при постоянной ошибке выдачи).
- **Логирование**: user_id, gift, цена в ешках, `star_cost`, статус, время,
  `transaction_id`, `idempotency_key`, причина отказа, `channel`.
- **Economic Control Center** `/admin/economy/gifts`: выручка, истрачено Stars
  по выданным, маржа, воронка pending/completed/cancelled.

**Архитектурная подготовка (код есть, нужен внешний ресурс/настройка):**
- **Реальная выдача `sendGift`** — за флагом `GIFTS_DELIVERY_ENABLED` + баланс
  Stars + реальные `telegram_gift_id`.
- **Синк каталога из `getAvailableGifts`** — метод не вызывается; реальные id и
  `star_count` пока заводятся вручную в админке.
- **Фоновый воркер дожатия pending** — выдача сейчас пробуется один раз при
  покупке; повторных авто-попыток нет (можно дожать вручную через refund/повтор).
- **Баланс «фонда» Gifts и донат Stars→ешки** — нужен `stars_ledger` (отдельный
  будущий этап); поэтому «Баланс фонда» = «—».

---

## 6. Аудит цепочки Gifts Shop V1 — что чем обусловлено

| Шаг цепочки | Работает после деплоя | Нужны Stars на балансе | Нужен `GIFTS_DELIVERY_ENABLED=true` | Заглушка / ручная настройка |
|---|:--:|:--:|:--:|---|
| Каталог + админка | ✅ | — | — | — |
| Витрина сайта `/gifts` | ✅ | — | — | — |
| Витрина бота `/подарки` | ✅ | — | — | — |
| Покупка за ешки (списание + pending) | ✅ | — | — | — |
| Возврат ешек | ✅ | — | — | — |
| P&L в Economic Control Center | ✅ | — | — | фонд/донат = «—» (нужен stars_ledger) |
| Реальная отправка подарка | — | ✅ | ✅ | + реальный `telegram_gift_id` |
| Проверка баланса бота перед выдачей | частично | ✅ | ✅ | зависит от версии aiogram |
| Синк каталога из Telegram | — | — | — | `getAvailableGifts` не вызывается (ручной ввод id) |
| Авто-дожатие pending | — | — | — | воркера нет (ручной разбор) |

Итого «реально работает на проде сразу»: весь путь до **оплаченного pending**
включительно + P&L. Реальная **отправка** включается отдельно, когда у бота есть
Stars, верные `telegram_gift_id` и поднят флаг.

---

## 7. Runbook эксплуатации (выполнять на VPS, где есть docker)

> На этой машине нет `docker`/`node` в PATH — команды ниже под прод/VPS.
> Все шаги, кроме включения выдачи, безопасны (деньги игрока возвращаются).

### Шаг 1. Миграции
```bash
docker compose exec bot alembic upgrade head     # дойдёт до 0021_gift_tx_tg_gift_kind
docker compose exec bot alembic current          # проверить HEAD
```

### Шаг 2. Тесты (smoke)
```bash
docker compose exec bot pytest -q
```

### Шаг 3. Заполнить каталог
Вариант А — через админку сайта `/admin/gifts` (рекомендуется): добавить позицию,
указать `name`, `code`, цену в ешках, `star_cost`, `stock`, при наличии —
реальный `telegram_gift_id` из getAvailableGifts.

Вариант Б — сид-миграция `0020_seed_gift_catalog` уже добавляет стартовые позиции
(применяется на шаге 1). Проверить:
```bash
docker compose exec db psql -U voznya -d voznya -c \
  "SELECT code,name,price_eshki,star_cost,stock,telegram_gift_id,is_active FROM gift_catalog ORDER BY sort_order;"
```

### Шаг 4. Тестовая покупка (выдача ещё выключена)
1. В чате с ботом: `/подарки` → нажать «Купить …».
2. Ожидаемо: ешки списались, ответ «оплачено, отправлю позже» (pending).
3. Проверить записи:
```bash
docker compose exec db psql -U voznya -d voznya -c \
  "SELECT id,status,recipient_user_id,item_code,idempotency_key,transaction_id,meta FROM gift_transactions ORDER BY id DESC LIMIT 5;"
docker compose exec db psql -U voznya -d voznya -c \
  "SELECT id,user_id,item_code,price,source,transaction_id FROM purchase_history WHERE source='gift' ORDER BY id DESC LIMIT 5;"
```

### Шаг 5. Тестовая выдача (реальные Stars!)
Предусловия: у бота есть баланс Stars ≥ `star_cost`, в позиции каталога стоит
**реальный** `telegram_gift_id`, версия aiogram поддерживает `send_gift`.
```bash
# в .env: GIFTS_DELIVERY_ENABLED=true
docker compose up -d   # перезапуск с новым флагом
```
Затем снова `/подарки` → купить. Ожидаемо: ответ «Подарок отправлен», в Telegram
у получателя появляется подарок; в БД: `gift_transactions.status='completed'`,
`meta.api_ok=true`, `star_balance_before/after`, у позиции `sold_count+1`.
Если подарок недоступен/ошибка — `status='cancelled'` + ешки возвращены.

### Шаг 6. Реальные данные в Economic Control Center
Открыть `/admin/economy/gifts`: проверить «Выручка (ешки)», «Истрачено Stars
(факт)», «Маржа», воронку «выдано / ждут / отменено». Числа должны совпасть с
тестовыми покупками/выдачами из шагов 4–5.

### Откат флага
Если что-то не так с выдачей — `GIFTS_DELIVERY_ENABLED=false` + `docker compose
up -d`. Покупки продолжат работать в режиме pending без траты Stars.
