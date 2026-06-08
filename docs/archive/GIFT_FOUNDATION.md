# GIFT & ECONOMY FOUNDATION — Возня

Слой передачи активов между игроками: подарки предметов и ешек, системные и
админские подарки, единые типы экономических событий. Стоит поверх готовых
систем — `inventory` (владение), `transactions` (деньги), `audit_log` (аудит),
`permissions` (доступ).

Статус (2026-06-06, проверено аудитом): **FOUNDATION ONLY.** Есть модель
`gift_transactions`, колонка `transferable`, модуль `economy_events`, права и
миграция 0011. НЕТ хендлеров дарения, проверки `transferable` в рантайме и
перевода предметов — ни один живой код не использует `gift_transactions`.
Зависит от инвентаря, который тоже foundation-only. UI, маркетплейс и кейсы —
НЕ делались.

Дата: 2026-06-06
Связанные документы: `INVENTORY_FOUNDATION.md`, `SHOP_FOUNDATION.md`,
`ADMIN_PLATFORM.md`, `FOUNDATION_STATUS.md`, `MINI_APP_PLAN.md`.

### Что НЕ тронуто (по требованию)
`users`, баланс (не дублируется — `users` + движение через `transactions`),
`inventory` остаётся единственным источником владения предметами, `transactions`
— единственный финансовый леджер, структура `audit_log` не менялась. OIDC,
`account_links`, auth — не тронуты. Связи логические по id/коду, без FK.

---

## 0. Что реализовано (код)

| Файл | Что |
|------|-----|
| `app/models/gift_transaction.py` | `gift_transactions` + константы `GIFT_KINDS`, `GIFT_TYPES`, `GIFT_STATUSES`. |
| `app/core/economy_events.py` | Канонические типы событий экономики + хелперы (`reason` для `transactions`). |
| `app/models/inventory_item.py` | Новая колонка `transferable` (можно ли дарить предмет). |
| `app/core/permissions.py` | Права `gift.view` (support+) и `gift.manage` (admin+). |
| `app/models/__init__.py` | Регистрация модели. |
| `migrations/versions/0011_gift_foundation.py` | Миграция: `gift_transactions` + `inventory_items.transferable`. |

> ⚠️ Не запускалось локально (нет рабочего Python/venv). Перед деплоем —
> `py_compile` + `alembic upgrade head`. CHECK-констрейнты и уникальный
> `idempotency_key` — стандартный PostgreSQL.

---

## 1. Gift system → `gift_transactions`

Один журнал на все виды передач. Что дарится — `kind`; происхождение — `gift_type`.

| Сценарий | kind | gift_type | sender | Эффект |
|----------|------|-----------|--------|--------|
| Подарок предмета игрок→игрок | `item` | `player` | игрок | revoke у отправителя + grant получателю в inventory |
| Подарок ешек игрок→игрок | `currency` | `player` | игрок | две проводки в transactions (−/+) |
| Системный подарок | `item`/`currency` | `system` | NULL | grant/начисление получателю (без списания у кого-либо) |
| Подарок от администрации | `item`/`currency` | `admin` | админ | как системный + запись в audit_log |

`gift_transactions` — это журнал факта, а не хранилище активов: предметы живут в
`inventory`, деньги — в `transactions`. Подарок их связывает.

### `gift_transactions` — реализовано
```
┌────────────────────┬─────────────┬──────────┬────────────────────────────┐
│ id                 │ BIGINT      │ PK       │ autoincrement              │
│ kind               │ VARCHAR(16) │ NOT NULL │ item / currency            │
│ gift_type          │ VARCHAR(16) │ NOT NULL │ player / system / admin    │
│ sender_user_id     │ BIGINT      │ NULL     │ NULL = системный           │
│ recipient_user_id  │ BIGINT      │ NOT NULL │ получатель                 │
│ item_code          │ VARCHAR(64) │ NULL     │ для kind=item              │
│ quantity           │ INTEGER     │ NOT NULL │ для kind=item (DEFAULT 1)  │
│ amount             │ BIGINT      │ NULL     │ для kind=currency (>0)     │
│ status             │ VARCHAR(16) │ NOT NULL │ completed/pending/cancelled│
│ idempotency_key    │ VARCHAR(64) │ UNIQUE   │ защита от двойной отправки │
│ transaction_id     │ BIGINT      │ NULL     │ → transactions.id          │
│ audit_id           │ BIGINT      │ NULL     │ → audit_log.id             │
│ meta               │ JSONB       │ NULL     │                            │
│ created_at         │ TIMESTAMPTZ │ NOT NULL │ DEFAULT now()              │
└────────────────────┴─────────────┴──────────┴────────────────────────────┘
CHECK: not_self (sender<>recipient), amount>0, kind_payload (item⇒item_code, currency⇒amount)
Индексы: ix_gift_recipient (recipient,created_at), ix_gift_sender (sender,created_at),
         uq_gift_idempotency_key (idempotency_key)
```

---

## 2. Item transfer flow

Передача предмета игрок→игрок — **одна транзакция БД** (атомарно):

```
POST /gifts  { kind:"item", recipient_id, item_code, idempotency_key }
──────────────────────────────────────────────────────────────────────
BEGIN;
  1. Идемпотентность: INSERT gift_transactions(..., idempotency_key) — если ключ
     уже есть, unique violation ⇒ это повтор, вернуть прежний результат (без
     второй передачи).
  2. Проверить предмет получателя-отправителя FOR UPDATE строку inventory
     отправителя:
       SELECT ... FROM inventory
        WHERE user_id=:sender AND item_code=:code FOR UPDATE;
     — нет строки или quantity<1 ⇒ «предмета нет» ⇒ ROLLBACK.
  3. Проверить ограничения (см. §4): предмет transferable, не экипирован.
  4. Снять у отправителя (атомарно, с условием наличия):
       UPDATE inventory SET quantity = quantity - :qty
        WHERE user_id=:sender AND item_code=:code AND quantity >= :qty;
       -- 0 строк ⇒ гонка/недостаточно ⇒ ROLLBACK.
       (quantity=0 ⇒ строку можно удалить)
  5. Выдать получателю: upsert inventory(recipient, item_code, source='gift'),
     стак quantity.
  6. inventory_history ×2: revoke у отправителя (delta=-qty), grant получателю
     (delta=+qty, source='gift'), обе с meta.gift_id.
  7. Зафиксировать gift_transactions.status='completed'.
COMMIT;
```

Для **подарка ешек** шаги 2–6 заменяются двумя проводками через `change_balance`:
списать у отправителя (проверка `balance >= amount`) и начислить получателю,
`reason='gift'`, обе в той же транзакции; `transaction_id` пишем в подарок.

Требования задачи — закрыты:
- **атомарность** — единая транзакция, любой провал = ROLLBACK;
- **защита от двойной передачи** — `idempotency_key` (uniq) + условные UPDATE;
- **нельзя передать несуществующий предмет** — `FOR UPDATE` + условие `quantity>=qty`;
- **запись в inventory_history** — шаг 6 (две строки);
- **запись в transactions при необходимости** — для валютных подарков (шаг-аналог).

---

## 3. Economy events → `transactions`

Все начисления/списания — через существующий леджер `transactions` с `reason` из
`app/core/economy_events.py`. Своей таблицы у событий нет: событие = строка
проводки. Баланс не дублируется (живёт в `users`, меняется `change_balance`).

| Тип события | `reason` | Знак amount | audit_log? |
|-------------|----------|-------------|:----------:|
| Награда (ферма/клад) | `reward` | + | — |
| Награда за событие | `event_reward` | + | — |
| Реферальная награда | `referral_reward` | + | — |
| Награда от админа | `admin_reward` | + | ✅ |
| Награда за дуэль | `duel_reward` | +/− | — |
| Семейная награда | `family_reward` | + | — |
| Покупка (магазин) | `purchase` | − | — |
| Подарок ешек | `gift` | −/+ (две строки) | если gift_type=admin/system |

Хелперы модуля: `is_economy_event(reason)` (валидация), `requires_audit(reason)`
(нужна ли запись в audit_log — сейчас для `admin_reward`).

Принцип: один тип события = один `reason`; детали (источник, кто инициировал,
ссылки) — в `meta` проводки. Это позволяет строить отчёты по экономике одним
запросом к `transactions`.

---

## 4. Gift restrictions

Проверяются в сервисе передачи (этап B), до списания, в той же транзакции:

| Правило | Как проверяется |
|---------|-----------------|
| Нельзя подарить непередаваемый предмет | `inventory_items.transferable = false` ⇒ отказ. Флаг — на каталоге (новая колонка). |
| Нельзя подарить экипированный предмет | строка `inventory.equipped = true` ⇒ отказ (сначала снять). Защищает от «подарил надетое». |
| Нельзя подарить предмет, которого нет | `SELECT ... FOR UPDATE` + `quantity >= qty`; иначе отказ. |
| Нельзя подарить себе | CHECK `ck_gift_not_self` на уровне БД. |
| Защита от гонок | `FOR UPDATE` на строке инвентаря отправителя + атомарный `UPDATE ... WHERE quantity >= :qty` (0 строк ⇒ откат) + уникальный `idempotency_key`. Двойной клик/параллельные запросы не спишут предмет дважды. |
| Лимиты/частота (анти-абьюз) | задел: rate-limit и проверки в сервисе; не часть схемы фундамента. |

Для валютных подарков аналогично: `balance >= amount` под блокировкой строки
пользователя (как в существующем `change_balance`).

---

## 5. Admin integration

Права в `app/core/permissions.py`:
- **`gift.view`** — у `support` и выше: просмотр истории подарков.
- **`gift.manage`** — у `admin` и выше: системные/админские подарки (выдать
  предмет или ешки игроку как подарок).

| Действие | Право | audit_log |
|----------|-------|-----------|
| Просмотр истории подарков | `gift.view` | — |
| Системный/админский подарок предмета | `gift.manage` | ✅ `gift.grant` |
| Системный/админский подарок ешек | `gift.manage` + `economy.add` | ✅ `gift.grant`/`economy.add` |
| Отмена «зависшего» подарка (будущее) | `gift.manage` | ✅ |

Админский подарок: `gift_type='admin'`, `sender_user_id`=админ, `audit_id`
ссылается на строку `audit_log` (action домена `gift.*`), как прочие
админ-действия. Обычный игровой подарок аудит не пишет (актора-админа нет).

---

## 6. Mini App compatibility

| Где | Как работают подарки |
|-----|----------------------|
| **Сайт** (`v0-voznya`) | На профиле игрока кнопка «Подарить»: предмет из своего инвентаря или ешки. `POST /gifts` под сессией. Полученные подарки видны в инвентаре/балансе сразу (общий источник). |
| **Mini App** | Тот же `POST /gifts` под Bearer-сессией (`MINI_APP_PLAN.md` §8). Раздел «Подарки»: входящие/исходящие — `GET /gifts/history`. |
| **Переиспользование** | `inventory`/`inventory_history` (предметы), `transactions`/`change_balance` (ешки), `permissions` (админ-подарки), `audit_log` (аудит). Никакой новой валюты, логина или хранилища активов. |

Mini App-план менять не нужно: подарки — раздел поверх готовых сессий и API.

---

## 7. API design (только проектирование)

Пользовательские (сессия игрока):
| Метод + путь | Назначение |
|--------------|-----------|
| `POST /gifts` | подарить предмет или ешки (`kind`, `recipient_id`, `item_code`/`amount`, `idempotency_key`); flow §2 |
| `GET /gifts/history` | мои входящие/исходящие подарки |

Админские (`ADMIN_PLATFORM.md`, право `gift.view`/`gift.manage`):
`GET /api/admin/gifts` (история/поиск), `POST /api/admin/gifts/grant`
(системный/админский подарок, пишет audit_log).

Правила: списание/выдачу делает только сервер в одной транзакции; запрос несёт
`idempotency_key`; запись в активы — только на бэке.

---

## 8. ER-схема

```
                          ┌────────────────────┐
       sender_user_id ───►│  gift_transactions │◄─── recipient_user_id
       (NULL=система)     │  PK id             │
                          │  kind/gift_type    │
                          │  item_code, amount │
   ┌─────────────┐        │  idempotency_key UQ│
   │   users     │◄───────┤  transaction_id ───┼──► transactions.id  (деньги)
   │ (баланс,    │        │  audit_id ─────────┼──► audit_log.id      (админ)
   │  не дублир.)│        └─────────┬──────────┘
   └─────────────┘                  │ item_code (для kind=item)
                                    ▼
                          ┌────────────────────┐
                          │  inventory_items   │  (+ новая колонка transferable)
                          └──────────┬─────────┘
                                     │ code
                                     ▼
   передача предмета пишет:  inventory (revoke+grant, source=gift)
                           + inventory_history ×2 (revoke / grant)

   Связи логические (по id/коду), физических FK нет — конвенция проекта.
```

---

## 9. Связи с существующими системами

- **inventory** — единственный источник владения. Подарок предмета = revoke у
  отправителя + grant получателю (`source='gift'`) в одной транзакции.
- **inventory_history** — две записи на передачу предмета (`event='revoke'` и
  `event='grant'`, `source='gift'`, ссылка на `gift_id` в meta).
- **transactions** — единственный финансовый леджер. Подарок ешек = две проводки
  (`reason='gift'`), `gift_transactions.transaction_id` ссылается на них. Баланс
  не дублируется.
- **economy_events** — канонические `reason` для всех начислений/списаний,
  включая `gift`, `reward`, `*_reward`, `purchase`.
- **audit_log** — системные/админские подарки (`gift.*`), связь по `audit_id`.
- **permissions** — `gift.view` / `gift.manage`.

Принцип: подарок — связующий журнал; активы, деньги, права и аудит
переиспользуются, не клонируются.

---

## 10. Список таблиц/изменений

| Объект | Статус | Назначение |
|--------|--------|-----------|
| `gift_transactions` | ✅ реализована | журнал подарков (предметы + ешки) |
| `inventory_items.transferable` | ✅ добавлена | можно ли дарить предмет |
| `app/core/economy_events.py` | ✅ реализован | типы событий экономики (поверх transactions) |
| `inventory`/`inventory_history`/`transactions`/`users`/`audit_log` | ✅ есть | не изменены структурно |

---

## 11. Roadmap реализации

**Этап A — gift/economy layer (этот проход, готово):**
- [x] `gift_transactions` (модель + миграция 0011)
- [x] `inventory_items.transferable`
- [x] `economy_events` (типы + хелперы)
- [x] права `gift.view` / `gift.manage`

**Этап B — сервис подарков (бот-сторона):**
- [ ] `app/services/gift.py`: transfer flow §2 (предмет: revoke+grant; ешки:
  две проводки) в одной транзакции, с проверками §4 и идемпотентностью
- [ ] хелпер записи `economy_events` в transactions (единый `change_balance`-путь)

**Этап C — пользовательское API (сайт/Mini App):**
- [ ] `POST /gifts`, `GET /gifts/history`

**Этап D — админ-подарки:**
- [ ] `GET /api/admin/gifts`, `POST /api/admin/gifts/grant` (с audit_log)

**Этап E — UI (после backend):**
- [ ] кнопка «Подарить» и раздел подарков на сайте и в Mini App

---

## 12. Что НЕ делалось (по требованию)
- ❌ UI подарков
- ❌ маркетплейс (P2P-торговля за деньги)
- ❌ кейсы/лутбоксы
- ❌ изменения в users, балансе, transactions(структуре), inventory(структуре),
  audit_log(структуре), OIDC, account_links, auth
