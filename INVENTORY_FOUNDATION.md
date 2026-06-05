# INVENTORY FOUNDATION — Возня

Центральный инвентарный слой: каталог предметов, владение, экипировка и история
движений. На нём встанут косметика, награды, магазин и будущие игровые механики.

Статус: **фундамент реализован в коде** (3 модели + миграция 0009). Магазин, UI и
реализация конкретных предметов — НЕ делались.

Дата: 2026-06-06
Связанные документы: `ADMIN_PLATFORM.md` (роли, audit_log), `FOUNDATION_STATUS.md`
(экономика, схема магазина), `MINI_APP_PLAN.md` (Mini App).

### Что НЕ тронуто (по требованию)
`users`, баланс, экономика, `transactions` (структура), OIDC, `account_links`,
`oidc_link_requests` — без изменений. Инвентарь — отдельный слой, ссылается на
`user_id` без внешних ключей (конвенция проекта). `admin_roles` и `audit_log`
используются опосредованно (через `actor_user_id`/`audit_id`), сами не меняются.

---

## 0. Что реализовано в этом проходе (код)

| Файл | Что |
|------|-----|
| `app/models/inventory_item.py` | Каталог `inventory_items` + константы `ITEM_TYPES`, `ITEM_RARITIES`, `EQUIP_SLOTS`. |
| `app/models/inventory.py` | Владение `inventory` + экипировка (частичный уник-индекс «1 на слот»). |
| `app/models/inventory_history.py` | Леджер `inventory_history` + `INVENTORY_SOURCES`, `INVENTORY_EVENTS`. |
| `app/models/__init__.py` | Регистрация моделей для Alembic. |
| `migrations/versions/0009_inventory_foundation.py` | Миграция: 3 таблицы + индексы. |

> ⚠️ Не запускалось локально (нет рабочего Python/venv на машине). Перед деплоем
> прогнать `py_compile` + `alembic upgrade head`. Частичный уникальный индекс —
> фича PostgreSQL (`postgresql_where`), на проде PostgreSQL это работает.

---

## 1. Inventory data model

Три таблицы, разделение ответственности:
- **`inventory_items`** — *что вообще существует* (справочник/каталог).
- **`inventory`** — *что есть у игрока сейчас* + что экипировано.
- **`inventory_history`** — *что когда-либо двигалось* (append-only).

Поддерживаемые виды (через `type` + `slot` + `payload`, без новых колонок):
титулы, рамки, бейджи, аватары, цвета профиля (`type=cosmetic`,
`payload.color`), коллекционные, лимитированные (`is_limited`+`max_supply`),
событийные и будущие игровые предметы.

### `inventory_items` (каталог) — реализовано
```
┌──────────────┬──────────────┬──────────┬───────────────────────────────────┐
│ id           │ INTEGER      │ PK       │ autoincrement                     │
│ code         │ VARCHAR(64)  │ UNIQUE   │ машинный код ("title_legend")     │
│ type         │ VARCHAR(16)  │ NOT NULL │ cosmetic/title/badge/frame/avatar/│
│              │              │          │ collectible/event                 │
│ slot         │ VARCHAR(16)  │ NULL     │ title/frame/badge/avatar или NULL │
│ rarity       │ VARCHAR(16)  │ NOT NULL │ common…legendary                  │
│ name         │ VARCHAR(128) │ NOT NULL │ отображаемое имя                  │
│ description  │ TEXT         │ NULL     │                                   │
│ payload      │ JSONB        │ NULL     │ {"color","image","text",…}        │
│ is_limited   │ BOOLEAN      │ NOT NULL │ лимитка?                          │
│ max_supply   │ INTEGER      │ NULL     │ NULL = безлимит                   │
│ is_active    │ BOOLEAN      │ NOT NULL │ доступен к выдаче/витрине          │
│ created_at   │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now()                     │
└──────────────┴──────────────┴──────────┴───────────────────────────────────┘
Индексы: uq_inventory_items_code (code), ix_inventory_items_type_active (type,is_active)
```

### `inventory` (владение + экипировка) — реализовано
```
┌──────────────┬──────────────┬──────────┬───────────────────────────────────┐
│ id           │ BIGINT       │ PK       │ autoincrement                     │
│ user_id      │ BIGINT       │ NOT NULL │ владелец (без FK)                 │
│ item_code    │ VARCHAR(64)  │ NOT NULL │ → inventory_items.code            │
│ slot         │ VARCHAR(16)  │ NULL     │ снимок слота при выдаче           │
│ quantity     │ INTEGER      │ NOT NULL │ DEFAULT 1, CHECK >= 0             │
│ equipped     │ BOOLEAN      │ NOT NULL │ экипирован сейчас?                │
│ source       │ VARCHAR(16)  │ NOT NULL │ shop/gift/admin/reward/event/migr │
│ payload      │ JSONB        │ NULL     │ серийный номер лимитки и т.п.      │
│ acquired_at  │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now()                     │
└──────────────┴──────────────┴──────────┴───────────────────────────────────┘
Ограничения/индексы:
  uq_inventory_user_item            UNIQUE (user_id, item_code)  — не дублируем, стакаем quantity
  uq_inventory_one_equipped_per_slot UNIQUE (user_id, slot) WHERE equipped=true AND slot IS NOT NULL
  ix_inventory_user                 (user_id)
  ck_inventory_quantity_nonneg      CHECK (quantity >= 0)
```

### `inventory_history` (леджер) — реализовано
```
┌────────────────┬──────────────┬──────────┬─────────────────────────────────┐
│ id             │ BIGINT       │ PK       │ autoincrement                   │
│ user_id        │ BIGINT       │ NOT NULL │                                 │
│ item_code      │ VARCHAR(64)  │ NOT NULL │ снимок кода                     │
│ delta          │ INTEGER      │ NOT NULL │ +получено / −снято / 0 для equip│
│ event          │ VARCHAR(16)  │ NOT NULL │ grant/revoke/purchase/use/equip/unequip │
│ source         │ VARCHAR(16)  │ NOT NULL │ shop/gift/admin/reward/event/migration │
│ actor_user_id  │ BIGINT       │ NULL     │ кто инициировал (админ/даритель)│
│ audit_id       │ BIGINT       │ NULL     │ → audit_log.id (если админ)     │
│ transaction_id │ BIGINT       │ NULL     │ → transactions.id (если покупка)│
│ meta           │ JSONB        │ NULL     │ {"to_user_id",…}                │
│ created_at     │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now()                   │
└────────────────┴──────────────┴──────────┴─────────────────────────────────┘
Индексы: ix_inventory_history_user (user_id,created_at), ix_inventory_history_item (item_code,created_at)
```

---

## 2. Item catalog — универсальная модель предметов

Один справочник `inventory_items` описывает все виды. Поведение задаётся полями,
а не отдельными таблицами на каждый вид:

| `type` | Экипируется? (`slot`) | Пример | Где спец-данные |
|--------|----------------------|--------|-----------------|
| `title` | `title` | «Легенда Возни» | `payload.text` |
| `frame` | `frame` | золотая рамка | `payload.image` |
| `badge` | `badge` | значок беты | `payload.image` |
| `avatar` | `avatar` | спец-аватар | `payload.image` |
| `cosmetic` | обычно NULL | цвет профиля | `payload.color` |
| `collectible` | NULL | карточка | — |
| `event` | NULL/слот | новогодний предмет | `payload`, `is_limited` |

Будущие игровые предметы добавляются новым `type` без миграции схемы (расширяем
кортеж `ITEM_TYPES` в коде).

### Редкость (`rarity`)
`common` → `uncommon` → `rare` → `epic` → `legendary`. Влияет на визуал (цвет
рамки/подсветки в UI) и ценность. Хранится строкой; набор — `ITEM_RARITIES`.

### Лимитированные предметы
`is_limited=true` + `max_supply=N`. Текущая «эмиссия» считается по
`inventory`/`inventory_history` (не дублируем счётчик в каталоге, чтобы не
рассинхронить). Проверка лимита при выдаче — в сервисном слое (этап B),
с блокировкой строки каталога или пересчётом в транзакции.

---

## 3. Equipment system — экипировка

Игрок владеет многими предметами, но активным в каждом слоте может быть только
один. Слоты: **title, frame, badge, avatar** (по одному).

Реализация ограничения — **частичный уникальный индекс** в `inventory`:
```sql
CREATE UNIQUE INDEX uq_inventory_one_equipped_per_slot
  ON inventory (user_id, slot)
  WHERE equipped = true AND slot IS NOT NULL;
```
Это значит: у игрока не может быть двух строк с `equipped=true` в одном слоте —
БД физически запретит, даже при гонке параллельных запросов «надеть второй
титул». Не-экипируемые предметы (`slot IS NULL`: collectible/event) под индекс
не попадают и не мешают.

### Операция equip (псевдокод, этап B)
```
В одной транзакции:
  1. снять текущий экипированный в этом слоте: UPDATE ... SET equipped=false
     WHERE user_id=:u AND slot=:s AND equipped=true
  2. надеть новый: UPDATE ... SET equipped=true WHERE user_id=:u AND item_code=:c
  3. лог: INSERT inventory_history(event='equip'/'unequip', delta=0)
```
Порядок «сначала снять, потом надеть» гарантирует, что индекс ни на мгновение не
нарушается.

---

## 4. Inventory history — логирование движений

Каждое получение/удаление/экипировка пишет строку в `inventory_history`.

Причины (`source`): **shop, gift, admin, reward, event, migration**.
События (`event`): **grant, revoke, purchase, use, equip, unequip**.

Связи без FK через id-ссылки:
- `audit_id` → строка `audit_log` (для админ-действий);
- `transaction_id` → строка `transactions` (для покупок за ешки).

Это даёт сквозную трассировку: по записи истории можно дойти и до денежной
проводки, и до управленческого аудита.

---

## 5. Admin compatibility

Через существующую админ-платформу (`ADMIN_PLATFORM.md`): права уже заведены в
`app/core/permissions.py` (`inventory.view/grant/revoke`).

| Действие | Право | Что происходит (в одной транзакции БД) |
|----------|-------|----------------------------------------|
| Выдать предмет | `inventory.grant` | upsert в `inventory` (стак quantity) + `inventory_history(event=grant, source=admin)` + `audit_log(action=inventory.grant)`; связать через `audit_id` |
| Отозвать предмет | `inventory.revoke` | уменьшить/удалить в `inventory` + `inventory_history(event=revoke, source=admin)` + `audit_log(action=inventory.revoke)` |
| Просмотр инвентаря | `inventory.view` | чтение `inventory` + `inventory_items` (без записи в audit) |

API уже спроектировано в `ADMIN_PLATFORM.md` §4: `GET /api/admin/inventory/[userId]`,
`POST /api/admin/inventory/grant`, `POST /api/admin/inventory/revoke`.

---

## 6. Mini App compatibility

| Где | Как используется inventory |
|-----|----------------------------|
| **Сайт** (`v0-voznya`) | Профиль `/profile/[id]` показывает экипированные предметы (титул/рамка/бейдж/аватар) — read-only выборка `inventory WHERE equipped` + join к `inventory_items` по `code`. |
| **Mini App** | Раздел «Инвентарь»: список предметов игрока, экипировка/снятие через `POST /inventory/equip|unequip` (Bearer-сессия из `MINI_APP_PLAN.md`). История — `GET /inventory/history`. |
| **Будущий магазин** | Покупка создаёт строку в `inventory` (`source=shop`) + `inventory_history(event=purchase, transaction_id=...)` + списание через `change_balance` (reason `purchase`). Каталог магазина переиспользует `inventory_items` (что продаётся = что существует). |

Совместимость с Mini App-планом: те же сессии, тот же `user_id`, новый раздел
поверх готового read-only/Bearer-слоя. Менять Mini App-фундамент не нужно.

---

## 7. API design (только проектирование)

Пользовательские роуты (сессия игрока, НЕ админские). Префикс показателен —
финально лягут под `/api/inventory/*` на сайте / Mini App.

| Метод + путь | Назначение | Тело / параметры | Пишет history |
|--------------|-----------|------------------|:-------------:|
| `GET /inventory` | список предметов игрока (+ экипировка) | — (uid из сессии) | — |
| `POST /inventory/equip` | экипировать предмет | `{ item_code }` | ✅ equip (+ unequip старого) |
| `POST /inventory/unequip` | снять предмет/слот | `{ item_code }` или `{ slot }` | ✅ unequip |
| `GET /inventory/history` | история движений игрока | пагинация | — |

Правила:
- Все читают/пишут только инвентарь ТЕКУЩЕГО пользователя (uid из сессии);
  чужой инвентарь — только через админ-API с правом `inventory.view`.
- `equip` валидирует: предмет принадлежит игроку, у него есть `slot`, предмет
  активен. Дальше — транзакция из §3.
- Запись в игровые таблицы только на сервере; прямого доступа из браузера нет.

---

## 8. ER-схема

```
   ┌─────────────────┐         code (string, без FK)        ┌──────────────────┐
   │ inventory_items │◄─────────────────────────────────────│    inventory     │
   │ (каталог)       │                                       │ (владение+экип.) │
   │ PK id, UQ code  │                                       │ PK id            │
   └─────────────────┘                                       │ UQ (user_id,     │
                                                              │     item_code)   │
                                                              │ partial UQ slot  │
                                                              └────────┬─────────┘
                                                          user_id      │ item_code
                                                                       ▼
   ┌─────────────┐   user_id   ┌──────────────────────┐      ┌──────────────────┐
   │   users     │◄────────────│  inventory_history   │      │  (логические      │
   │ (PK user_id)│             │  (леджер движений)   │      │   ссылки по id)   │
   └─────────────┘             │  audit_id ───────────┼─────►│  audit_log.id     │
                               │  transaction_id ─────┼─────►│  transactions.id  │
                               └──────────────────────┘      └──────────────────┘

   users / баланс / transactions(структура) / OIDC / account_links — НЕ изменены.
   Все связи логические (по id/коду), физических FK нет — конвенция проекта.
```

---

## 9. Список таблиц

| Таблица | Статус | Назначение |
|---------|--------|-----------|
| `inventory_items` | ✅ реализована | каталог определений предметов |
| `inventory` | ✅ реализована | владение игроком + экипировка |
| `inventory_history` | ✅ реализована | append-only леджер движений |
| `users`, `transactions`, `admin_roles`, `audit_log` | ✅ существуют | не изменены |
| `shop_products`/`shop_orders`/`purchase_history` | 📋 будущее | магазин (FOUNDATION_STATUS.md §5) |

---

## 10. Список API
Пользовательские: `GET /inventory`, `POST /inventory/equip`,
`POST /inventory/unequip`, `GET /inventory/history`.
Админские (см. `ADMIN_PLATFORM.md`): `GET /api/admin/inventory/[userId]`,
`POST /api/admin/inventory/grant`, `POST /api/admin/inventory/revoke`.

---

## 11. Жизненный цикл предмета

```
 [Каталог] admin создаёт определение в inventory_items (is_active=true)
     │
     ▼
 [Получение] игрок получает предмет → строка в inventory (source=...) 
             + inventory_history(event=grant/purchase, delta=+1)
     │              ├─ покупка: + transactions(reason=purchase), history.transaction_id
     │              ├─ админ:   + audit_log(inventory.grant),    history.audit_id
     │              ├─ награда: source=reward (без денег)
     │              └─ подарок: source=gift, meta.to_user_id (будущее)
     ▼
 [Экипировка] equip/unequip → inventory.equipped + history(event=equip/unequip, delta=0)
     │         (частичный уник-индекс: 1 активный на слот)
     ▼
 [Отзыв/трата] revoke/use → quantity−− или удаление строки
             + inventory_history(event=revoke/use, delta=−N)
             + admin: audit_log(inventory.revoke)
```

---

## 12. Связь с audit_log и transactions

- **transactions** (валюта): покупка предмета списывает ешки одной проводкой
  (`reason=purchase`), `inventory_history.transaction_id` ссылается на неё.
  Выдача/награда/админ-грант денег НЕ двигают — в transactions не пишут.
- **audit_log** (управление): любое админ-действие над инвентарём пишет строку
  (`inventory.grant`/`inventory.revoke`), `inventory_history.audit_id` ссылается
  на неё. Снимок роли и причина — в audit_log.
- **inventory_history** (предметы): пишется ВСЕГДА при движении предмета, вне
  зависимости от источника. Это первичный леджер инвентаря; две ссылки выше —
  для сквозной трассировки.

Принцип: одна бизнес-операция = одна транзакция БД, затрагивающая нужный набор
леджеров атомарно.

---

## 13. Roadmap

**Этап A — фундамент (этот проход, готово):**
- [x] `inventory_items`, `inventory`, `inventory_history` (модели + миграция 0009)
- [x] константы типов/редкости/слотов/источников/событий
- [x] ограничение «1 экипированный на слот» (частичный уник-индекс)

**Этап B — сервис/репозиторий инвентаря (бот-сторона):**
- [ ] `app/repositories/inventory.py`: grant, revoke, equip, unequip, list,
  history (всё в транзакциях, с записью в history)
- [ ] проверка лимита `max_supply` при выдаче
- [ ] хелпер связки с `audit_log`/`transactions`

**Этап C — пользовательское API (сайт/Mini App):**
- [ ] `GET /inventory`, `POST /inventory/equip|unequip`, `GET /inventory/history`
- [ ] показ экипировки в `/profile/[id]`

**Этап D — админ-инвентарь:**
- [ ] `GET /api/admin/inventory/[userId]`, `grant`, `revoke` (с audit_log)

**Этап E — магазин и подарки (после инвентаря, ОТДЕЛЬНО):**
- [ ] `shop_products`/`shop_orders` (FOUNDATION_STATUS.md §5), покупка →
  inventory(source=shop) + transactions(purchase)
- [ ] подарки: перевод предмета между игроками (source=gift, meta.to_user_id),
  две записи history; механику и анти-абьюз проектировать отдельно

---

## 14. Что НЕ делалось (по требованию)
- ❌ магазин (только точки связи в roadmap)
- ❌ UI (ни сайт, ни Mini App)
- ❌ реализация конкретных предметов/каталога (только структура)
- ❌ изменения в users, балансе, экономике, transactions(структуре), OIDC,
  account_links
- ❌ подарки (только зарезервирован `source=gift` и план)
