# SHOP FOUNDATION — Возня

Магазин поверх готовых систем: каталог предметов (`inventory_items`), владение
(`inventory`), финансы (`transactions`), аудит (`audit_log`), права
(`permissions.py`). Магазин ничего из этого не дублирует — только добавляет
витрину, цену и историю покупок.

Статус: **shop layer реализован в коде** (3 модели + миграция 0010). UI магазина,
подарки и маркетплейс — НЕ делались.

Дата: 2026-06-06
Связанные документы: `INVENTORY_FOUNDATION.md`, `ADMIN_PLATFORM.md`,
`FOUNDATION_STATUS.md`, `MINI_APP_PLAN.md`.

### Что НЕ тронуто (по требованию)
`users`, баланс (не дублируется — остаётся в `users`, движется через
`transactions`), `inventory*` (единственный источник владения предметами),
структура `transactions`/`audit_log`, OIDC, `account_links`, auth. Связи —
логические по id/коду, без FK (конвенция проекта).

---

## 0. Что реализовано (код)

| Файл | Что |
|------|-----|
| `app/models/shop_category.py` | `shop_categories` — разделы витрины. |
| `app/models/shop_offer.py` | `shop_offers` — товар: предмет + цена + лимит/сезон + sold_count. |
| `app/models/purchase_history.py` | `purchase_history` — история покупок + `PURCHASE_SOURCES`. |
| `app/models/__init__.py` | Регистрация моделей для Alembic. |
| `migrations/versions/0010_shop_foundation.py` | Миграция: 3 таблицы + индексы + защита от двойной покупки. |

Права магазина уже были заведены на этапе админ-платформы
(`app/core/permissions.py`: `shop.view`, `shop.manage`) — новых прав не
требуется.

> ⚠️ Не запускалось локально (нет рабочего Python/venv). Перед деплоем —
> `py_compile` + `alembic upgrade head`. Частичные уникальные индексы и `meta ->>`
> — фичи PostgreSQL, на проде работают.

---

## 1. Shop catalog

Разделение ответственности:
- **`inventory_items`** (уже есть) — *что вообще существует* (титул/рамка/бейдж/
  аватар/коллекционка/событийный). Источник правды о предмете.
- **`shop_offers`** — *что и почём продаётся*. Ссылается на предмет по
  `item_code`. Один предмет можно продавать разными офферами (разные периоды/
  цены/лимиты).
- **`shop_categories`** — презентационная группировка офферов на витрине.

Все типы предметов поддержаны автоматически — оффер не знает тип, он берёт
предмет из каталога. Лимитированные/сезонные — это флаги оффера (см. §3).

### `shop_categories` — реализовано
```
┌────────────┬─────────────┬──────────┬───────────────────────────────┐
│ id         │ INTEGER     │ PK       │ autoincrement                 │
│ slug       │ VARCHAR(32) │ UNIQUE   │ "titles"/"frames"/"seasonal"  │
│ name       │ VARCHAR(64) │ NOT NULL │ отображаемое имя              │
│ sort_order │ INTEGER     │ NOT NULL │ порядок на витрине            │
│ is_active  │ BOOLEAN     │ NOT NULL │ скрытие раздела               │
│ created_at │ TIMESTAMPTZ │ NOT NULL │ DEFAULT now()                 │
└────────────┴─────────────┴──────────┴───────────────────────────────┘
```

### `shop_offers` — реализовано
```
┌────────────────┬─────────────┬──────────┬───────────────────────────────┐
│ id             │ INTEGER     │ PK       │ autoincrement                 │
│ item_code      │ VARCHAR(64) │ NOT NULL │ → inventory_items.code        │
│ category_slug  │ VARCHAR(32) │ NULL     │ → shop_categories.slug        │
│ price          │ BIGINT      │ NOT NULL │ цена в ешках, CHECK >= 0      │
│ is_limited     │ BOOLEAN     │ NOT NULL │ лимитированный тираж?         │
│ max_supply     │ INTEGER     │ NULL     │ тираж (NULL=безлимит)         │
│ sold_count     │ INTEGER     │ NOT NULL │ продано, CHECK >= 0           │
│ per_user_limit │ INTEGER     │ NULL     │ лимит на игрока (NULL=без)    │
│ is_seasonal    │ BOOLEAN     │ NOT NULL │ сезонный?                     │
│ starts_at      │ TIMESTAMPTZ │ NULL     │ начало окна продаж            │
│ ends_at        │ TIMESTAMPTZ │ NULL     │ конец окна продаж             │
│ is_active      │ BOOLEAN     │ NOT NULL │ ручное вкл/выкл               │
│ created_at     │ TIMESTAMPTZ │ NOT NULL │ DEFAULT now()                 │
│ updated_at     │ TIMESTAMPTZ │ NOT NULL │ onupdate now()                │
└────────────────┴─────────────┴──────────┴───────────────────────────────┘
CHECK: price>=0, sold_count>=0, (max_supply IS NULL OR sold_count<=max_supply)
Индексы: ix_shop_offers_category_active (category_slug,is_active), ix_shop_offers_item (item_code)
```

Финальная доступность к покупке (вычисляется в запросе/сервисе):
```
is_active = true
  AND (is_limited = false OR sold_count < max_supply)
  AND (is_seasonal = false OR now() BETWEEN starts_at AND ends_at)
```

### `purchase_history` — реализовано
```
┌────────────────┬─────────────┬──────────┬───────────────────────────────┐
│ id             │ BIGINT      │ PK       │ autoincrement                 │
│ user_id        │ BIGINT      │ NOT NULL │ кто купил                     │
│ offer_id       │ INTEGER     │ NOT NULL │ → shop_offers.id              │
│ item_code      │ VARCHAR(64) │ NOT NULL │ снимок предмета               │
│ price          │ BIGINT      │ NOT NULL │ снимок цены                   │
│ quantity       │ INTEGER     │ NOT NULL │                               │
│ source         │ VARCHAR(16) │ NOT NULL │ shop/admin/event              │
│ transaction_id │ BIGINT      │ NULL     │ → transactions.id             │
│ audit_id       │ BIGINT      │ NULL     │ → audit_log.id (админ-выдача) │
│ meta           │ JSONB       │ NULL     │ {"unique": true} для «1 на руки»│
│ created_at     │ TIMESTAMPTZ │ NOT NULL │ DEFAULT now()                 │
└────────────────┴─────────────┴──────────┴───────────────────────────────┘
Индексы: ix_purchase_history_user, ix_purchase_history_offer
  uq_purchase_user_offer_unique UNIQUE (user_id, offer_id) WHERE (meta->>'unique')='true'
```

---

## 2. Purchase flow

Вся покупка — **одна транзакция БД** (атомарность: либо всё, либо ничего).

```
POST /shop/offers/{id}/buy   (uid из сессии)
──────────────────────────────────────────────────────────────────────
BEGIN;
  1. Прочитать оффер FOR UPDATE (или полагаться на атомарный UPDATE в п.3).
     Проверить: is_active, окно сезона, цена.
  2. Проверить лимит на игрока (если per_user_limit задан): COUNT в
     purchase_history по (user_id, offer_id) < per_user_limit.
  3. ЕСЛИ лимитка — атомарно занять слот тиража:
       UPDATE shop_offers
          SET sold_count = sold_count + 1
        WHERE id = :offer_id
          AND (max_supply IS NULL OR sold_count < max_supply)
          AND is_active = true;
     -- 0 строк обновлено ⇒ распродано/выключено ⇒ ROLLBACK, отказ.
  4. Списать ешки через существующую экономику (change_balance):
       проверка balance >= price; UPDATE users; INSERT transactions
       (reason='purchase', amount = -price, meta={offer_id,item_code}).
     -- недостаточно средств ⇒ ROLLBACK, отказ.
  5. Выдать предмет в inventory (единственный источник владения):
       upsert inventory (user_id, item_code, source='shop', slot из каталога),
       стак quantity.
  6. INSERT inventory_history(event='purchase', source='shop', delta=+qty,
       transaction_id = :tx_id).
  7. INSERT purchase_history(user_id, offer_id, item_code, price, source='shop',
       transaction_id = :tx_id, meta = {"unique": true} если per_user_limit=1).
COMMIT;
```

Требования задачи — закрыты:
- **атомарность** — единая транзакция, любой провал = ROLLBACK;
- **нет двойной покупки лимитки** — два барьера (см. §3);
- **списание ешек** — через существующий `change_balance` → `transactions`,
  баланс не дублируется;
- **выдача в inventory** — п.5, единственный источник владения;
- **запись в transactions** — п.4;
- **запись в inventory_history** — п.6;
- **запись в audit_log** — только для админ-выдачи (см. §5), у обычной покупки
  актора-админа нет.

---

## 3. Limited items + защита от гонок

Поля оффера: `is_limited`, `max_supply`, `sold_count`, остаток =
`max_supply − sold_count`. Продажи отключаются, когда `sold_count = max_supply`
(условие доступности из §1 перестаёт выполняться).

Два независимых барьера против гонок и двойной покупки:

**Барьер 1 — атомарный условный UPDATE тиража (глобальный лимит).**
```sql
UPDATE shop_offers
   SET sold_count = sold_count + 1
 WHERE id = :offer_id
   AND is_active = true
   AND (max_supply IS NULL OR sold_count < max_supply);
```
PostgreSQL сериализует конкурентные UPDATE одной строки. При гонке за последний
экземпляр ровно один запрос обновит строку (вернёт 1), остальные получат 0 строк
и откатятся. Никаких «продали больше тиража». CHECK
`sold_count <= max_supply` — страховка на уровне схемы.

**Барьер 2 — частичный уникальный индекс (лимит «1 на руки»).**
Для офферов с `per_user_limit = 1` flow ставит `meta = {"unique": true}` в
`purchase_history`. Индекс `uq_purchase_user_offer_unique (user_id, offer_id)
WHERE (meta->>'unique')='true'` физически запрещает вторую такую строку — даже
два одновременных запроса одного игрока не пройдут оба (один словит unique
violation → ROLLBACK).

Для `per_user_limit > 1` — проверка COUNT в той же транзакции (п.2 flow);
строгий партирный лимит при необходимости усиливается advisory-lock по
(user_id, offer_id).

---

## 4. Shop history → `purchase_history`

Хранит требуемое: кто (`user_id`), что (`offer_id` + `item_code`), за сколько
(`price` — снимок), когда (`created_at`), источник (`source`: shop/admin/event).
Плюс ссылки на финансовую проводку (`transaction_id`) и админ-аудит
(`audit_id`) для сквозной трассировки. Append-only, не редактируется.

---

## 5. Admin integration

Через готовую админ-платформу. Права уже есть в `app/core/permissions.py`:

| Действие | Право | Эффект |
|----------|-------|--------|
| Включить товар | `shop.manage` | `shop_offers.is_active = true` + `audit_log(shop.offer.update)` |
| Отключить товар | `shop.manage` | `is_active = false` + audit |
| Изменить цену | `shop.manage` | `UPDATE price` + audit (meta: old/new) |
| Изменить описание/категорию/сезон | `shop.manage` | `UPDATE` + audit |
| Создать/удалить оффер | `shop.manage` | INSERT/`is_active=false` + audit |
| Статистика продаж | `shop.view` | агрегаты по `purchase_history`/`sold_count` |
| Админ-выдача предмета | `inventory.grant` | прямой grant в inventory; при желании — `purchase_history(source='admin', price=0, audit_id=...)` |

Все мутации витрины пишут `audit_log` (action домена `shop.*`), как и прочие
админ-действия. Меняет цены/доступность только `admin`/`owner` (право
`shop.manage`); `support`/`moderator` имеют лишь `shop.view`.

API админки уже намечено в `ADMIN_PLATFORM.md` §4
(`/api/admin/shop/products` → здесь это `shop_offers`).

---

## 6. Mini App compatibility

| Где | Как работает магазин |
|-----|----------------------|
| **Сайт** (`v0-voznya`) | Витрина: `GET /shop` (категории + активные офферы с join к `inventory_items` за именем/картинкой/редкостью). Покупка — `POST /shop/offers/{id}/buy` под сессией. Баланс показывается из `users` (как сейчас). |
| **Mini App** | Те же эндпоинты под Bearer-сессией (`MINI_APP_PLAN.md` §8). Покупка → предмет сразу виден в разделе «Инвентарь» (`GET /inventory`), т.к. источник владения общий. |
| **Переиспользование** | Каталог `inventory_items`, выдача в `inventory`, списание через `change_balance`/`transactions`, права из `permissions.py`, история через `purchase_history` + `inventory_history`. Магазин не вводит свою валюту, свой логин или своё хранилище предметов. |

Mini App-план менять не нужно: магазин — ещё один раздел поверх готовых
сессий и API.

---

## 7. API design (только проектирование)

Пользовательские (сессия игрока):
| Метод + путь | Назначение |
|--------------|-----------|
| `GET /shop` | категории + доступные офферы |
| `GET /shop/offers/{id}` | детали оффера (цена, остаток, окно) |
| `POST /shop/offers/{id}/buy` | покупка (flow §2); идемпотентность по `Idempotency-Key` рекомендуется |
| `GET /shop/history` | мои покупки (`purchase_history` по uid) |

Админские (см. `ADMIN_PLATFORM.md`, право `shop.view`/`shop.manage`):
`GET/POST/PATCH /api/admin/shop/offers`, `.../categories`,
`GET /api/admin/shop/stats`.

Правила: цену/доступность вычисляет и применяет только сервер; покупка — POST
под сессией; запись в игровые таблицы только на бэке.

---

## 8. ER-схема

```
 ┌──────────────────┐      item_code       ┌──────────────┐
 │ inventory_items  │◄─────────────────────│  shop_offers │
 │ (каталог, есть)  │                       │  PK id       │
 └──────────────────┘   ┌──category_slug───►│  price,limit │
                        │                    │  sold_count  │
 ┌──────────────────┐   │                    └──────┬───────┘
 │ shop_categories  │◄──┘                    offer_id│
 │  PK id, UQ slug  │                                ▼
 └──────────────────┘                       ┌──────────────────┐
                                             │ purchase_history │
 ┌─────────────┐  user_id                    │  PK id           │
 │   users     │◄───────────────────────────│  user_id         │
 │ (баланс,    │                             │  transaction_id ─┼──► transactions.id
 │  не дублир.)│                             │  audit_id ───────┼──► audit_log.id
 └─────────────┘                             └────────┬─────────┘
                                                       │ (та же транзакция БД)
        покупка пишет также:                           ▼
   transactions(reason=purchase, -price)  +  inventory(source=shop)  +  inventory_history(event=purchase)

 Связи логические (по id/коду), физических FK нет — конвенция проекта.
```

---

## 9. Связи с существующими системами

- **inventory** — единственный источник владения. Покупка выдаёт предмет сюда
  (`source='shop'`); экипировка/отображение работают как в `INVENTORY_FOUNDATION.md`.
- **transactions** — единственный источник финансовой истории. Покупка списывает
  ешки одной проводкой `reason='purchase'`; баланс не дублируется (живёт в
  `users`). `purchase_history.transaction_id` ссылается на проводку.
- **inventory_history** — движение предмета при покупке (`event='purchase'`),
  связано через `transaction_id`.
- **audit_log** — управленческие действия над витриной (`shop.*`) и админ-выдача
  (`purchase_history.audit_id`).
- **permissions** — доступ к управлению (`shop.manage`) и просмотру (`shop.view`).

Принцип: магазин — тонкий слой витрины и истории; деньги, предметы, права и
аудит он переиспользует, не клонируя.

---

## 10. Список таблиц

| Таблица | Статус | Назначение |
|---------|--------|-----------|
| `shop_categories` | ✅ реализована | разделы витрины |
| `shop_offers` | ✅ реализована | товары (предмет + цена + лимит/сезон) |
| `purchase_history` | ✅ реализована | история покупок |
| `inventory_items`/`inventory`/`inventory_history` | ✅ есть | каталог/владение/движения |
| `transactions`/`users`/`audit_log` | ✅ есть | деньги/баланс/аудит — не изменены |

---

## 11. Roadmap реализации

**Этап A — shop layer (этот проход, готово):**
- [x] `shop_categories`, `shop_offers`, `purchase_history` (модели + миграция 0010)
- [x] лимиты, сезонность, sold_count, защита от гонок (UPDATE + частичные индексы)

**Этап B — сервис покупки (бот-сторона):**
- [ ] `app/repositories/shop.py` + `app/services/purchase.py`: flow §2 в одной
  транзакции (тираж → списание → inventory → история)
- [ ] переиспользовать `change_balance` (reason='purchase')
- [ ] идемпотентность покупки

**Этап C — пользовательское API (сайт/Mini App):**
- [ ] `GET /shop`, `GET /shop/offers/{id}`, `POST /shop/offers/{id}/buy`,
  `GET /shop/history`

**Этап D — админ-магазин:**
- [ ] `/api/admin/shop/offers|categories` (CRUD под `shop.manage` + audit_log)
- [ ] `GET /api/admin/shop/stats` (продажи)

**Этап E — UI (после backend):**
- [ ] витрина и карточка оффера на сайте
- [ ] раздел магазина в Mini App
- [ ] баланс/после-покупки UX

---

## 12. Что НЕ делалось (по требованию)
- ❌ UI магазина (только API-контур и roadmap)
- ❌ подарки
- ❌ маркетплейс (P2P-торговля)
- ❌ изменения в users, балансе, transactions(структуре), inventory(структуре),
  OIDC, account_links, auth
