# ADMIN PLATFORM FOUNDATION — Возня

Фундамент админ-платформы: роли, аудит, сессии, разделы панели, единая система
транзакций и заготовка инвентаря. На этом фундаменте позже встанут магазин,
инвентарь, модерация и Mini App.

Статус: **фундамент частично реализован** (роли, аудит, права — код + миграция).
Магазин, инвентарь, фронт админки — **только проект**, без реализации.

Дата: 2026-06-06
Связанные документы: `FOUNDATION_STATUS.md` (auth-аудит, схема магазина),
`MINI_APP_PLAN.md` (Mini App).

---

## 0. Что реализовано в этом проходе (код)

| Файл | Что |
|------|-----|
| `app/models/admin_role.py` | Модель `AdminRole` (таблица `admin_roles`) + список `ADMIN_ROLES`. |
| `app/models/audit_log.py` | Модель `AuditLog` (таблица `audit_log`). |
| `app/core/permissions.py` | Единый источник прав: каталог `PERM_*`, карта `ROLE_PERMISSIONS`, `has_permission`, `can_manage_role`. |
| `app/models/__init__.py` | Регистрация новых моделей для Alembic. |
| `migrations/versions/0008_admin_platform.py` | Миграция: создание `admin_roles` и `audit_log` с индексами. |

**Только проект (без кода):** `admin_sessions` (решено НЕ создавать — см. §3),
таблицы магазина/инвентаря, фронт админки, API-роуты.

> ⚠️ Не запускалось локально: на машине нет рабочего Python (Windows Store
> заглушка) и venv. Перед деплоем — `py_compile` + `alembic upgrade head`
> (см. чек-лист в `FOUNDATION_STATUS.md`).

---

## 1. Система ролей

Четыре роли с наследованием прав по иерархии: **owner > admin > moderator >
support**. Хранятся в `admin_roles` (один игрок — одна роль). Источник правды о
правах — `app/core/permissions.py` (используется и ботом, и сайтом).

### Права по ролям (матрица)

| Право (`PERM_*`) | support | moderator | admin | owner |
|------------------|:-------:|:---------:|:-----:|:-----:|
| `dashboard.view` | ✅ | ✅ | ✅ | ✅ |
| `players.view` | ✅ | ✅ | ✅ | ✅ |
| `economy.view` | ✅ | ✅ | ✅ | ✅ |
| `inventory.view` | ✅ | ✅ | ✅ | ✅ |
| `shop.view` | ✅ | ✅ | ✅ | ✅ |
| `moderation.view` | ✅ | ✅ | ✅ | ✅ |
| `players.edit` | — | ✅ | ✅ | ✅ |
| `moderation.ban` (бан/разбан) | — | ✅ | ✅ | ✅ |
| `logs.view` (аудит-лог) | — | ✅ | ✅ | ✅ |
| `economy.add` (выдать ешки) | — | — | ✅ | ✅ |
| `economy.remove` (снять ешки) | — | — | ✅ | ✅ |
| `inventory.grant` (выдать предмет) | — | — | ✅ | ✅ |
| `inventory.revoke` (удалить предмет) | — | — | ✅ | ✅ |
| `shop.manage` (CRUD товаров) | — | — | ✅ | ✅ |
| `roles.manage` (назначать роли) | — | — | — | ✅ |

Кратко:
- **support** — «только смотреть»: видит игроков, балансы, инвентарь, витрину,
  очередь модерации. Ничего не меняет. Для первой линии поддержки.
- **moderator** — модерация (бан/разбан), правка профилей/заметок, доступ к
  логам. Не трогает деньги и предметы.
- **admin** — экономика (выдать/снять ешки), инвентарь (выдать/удалить предмет),
  управление магазином. Полный операционный доступ.
- **owner** — всё + единственный, кто управляет ролями.

### Bootstrap и связь с текущим `ADMIN_IDS`
- `ADMIN_IDS` (env, уже есть в `app/config.py`) остаётся «аварийным»
  суперпользователем уровня кода: пользователи из него считаются `owner`
  даже без строки в `admin_roles`. Это решает курицу-и-яйцо (первый owner) и
  даёт доступ, если БД-роли испорчены.
- Повседневные роли — в `admin_roles`. `owner` через панель/бот назначает
  admin/moderator/support.
- `can_manage_role` запрещает выдавать роль ≥ своего ранга (owner не плодит
  owner'ов через UI; новый owner — только через `ADMIN_IDS`).

### Резолвинг роли (псевдокод, единый для бота и сайта)
```
def resolve_role(user_id):
    if user_id in ADMIN_IDS:        # bootstrap-owner
        return "owner"
    row = SELECT role FROM admin_roles WHERE user_id = :user_id
    return row.role if row else None   # None = обычный игрок
```

---

## 2. Audit log

Любое значимое действие администратора пишет append-only строку в `audit_log`.
Таблица не редактируется (только вставка; retention-чистка очень старых записей
опциональна). Реализована: `app/models/audit_log.py`.

### Структура таблицы `audit_log`
```
┌────────────────┬───────────────┬──────────┬──────────────────────────────────┐
│ Колонка        │ Тип           │ NULL     │ Назначение                       │
├────────────────┼───────────────┼──────────┼──────────────────────────────────┤
│ id             │ BIGINT        │ NOT NULL │ PK, autoincrement                │
│ actor_user_id  │ BIGINT        │ NOT NULL │ кто сделал (user_id админа)      │
│ actor_role     │ VARCHAR(16)   │ NULL     │ снимок роли актора на тот момент │
│ action         │ VARCHAR(48)   │ NOT NULL │ "<домен>.<глагол>" (см. ниже)    │
│ target_user_id │ BIGINT        │ NULL     │ над каким игроком                │
│ target_type    │ VARCHAR(32)   │ NULL     │ тип цели (item/role/transaction) │
│ target_id      │ VARCHAR(64)   │ NULL     │ id цели, если не игрок           │
│ amount         │ BIGINT        │ NULL     │ сумма ешек (+/−) для денежных    │
│ reason         │ TEXT          │ NULL     │ комментарий админа               │
│ meta           │ JSONB         │ NULL     │ детали (old/new role, payload)   │
│ ip             │ VARCHAR(45)   │ NULL     │ IP (для панели; бот → NULL)      │
│ created_at     │ TIMESTAMPTZ   │ NOT NULL │ DEFAULT now()                    │
└────────────────┴───────────────┴──────────┴──────────────────────────────────┘
Индексы:
  ix_audit_log_actor  (actor_user_id, created_at)
  ix_audit_log_target (target_user_id, created_at)
  ix_audit_log_action (action, created_at)
```

### Каталог действий (`action`)
| action | Пример | amount | target |
|--------|--------|:------:|--------|
| `economy.add` | выдача ешек | + | игрок |
| `economy.remove` | снятие ешек | − | игрок |
| `inventory.grant` | выдача предмета | — | игрок + item |
| `inventory.revoke` | удаление предмета | — | игрок + item |
| `role.change` | изменение роли | — | игрок (meta: old/new) |
| `player.ban` | бан | — | игрок |
| `player.unban` | разбан | — | игрок |
| `shop.product.create/update/delete` | управление витриной | — | product |

### Связь audit_log ↔ transactions
- `transactions` — леджер **валюты** (что произошло с балансом).
- `audit_log` — управленческий **контекст** (кто из админов инициировал и
  почему).
- Денежное админ-действие пишет в ОБЕ таблицы в одной транзакции БД: строка в
  `transactions` (reason `admin_add`/`admin_remove`) + строка в `audit_log`
  (`economy.add`/`economy.remove`), при желании связанные через
  `audit_log.meta.transaction_id`.

---

## 3. admin_sessions и admin_permissions — решение

Вопрос задачи: использовать ли существующий JWT, как определять права, как
защищать админку. Ответ — **не плодить отдельные сущности**:

### admin_sessions — НЕ создаём отдельную таблицу
**Переиспользуем существующую сессию сайта** (`voznya_session` JWT, см.
`FOUNDATION_STATUS.md` §3). Причины:
- сессия уже привязана к настоящему `user_id` (через OIDC+account_links или
  Login Widget или Mini App) — этого достаточно, чтобы узнать, кто админ;
- отдельный логин для админки = вторая поверхность атаки и дублирование;
- роль — это свойство пользователя, а не отдельной «админ-сессии».

**Что меняется в JWT:** ничего в структуре. Роль НЕ зашиваем в токен (иначе при
понижении роли старый токен останется «админским» до `exp`). Роль читается из
`admin_roles` на КАЖДЫЙ админ-запрос — так отзыв прав мгновенный.

Усиление для админки (рекомендуется, не обязательно для фундамента):
- более короткий TTL и/или повторная проверка свежести для админ-действий;
- опционально — «step-up»: для критичных операций (выдача крупных сумм, смена
  ролей) требовать свежий вход/2FA. Это будущее расширение, не блокирует старт.

### admin_permissions — НЕ создаём таблицу
Права детерминированы ролью и заданы в коде (`app/core/permissions.py`). Таблица
разрешений нужна только при динамических, настраиваемых через UI правах — сейчас
это оверинжиниринг. Если понадобится — карта `ROLE_PERMISSIONS` переносится в
БД без изменения вызовов `has_permission`.

### Как защищается админка (слои)
1. **Аутентификация** — есть сессия (`getSession()` / Bearer для Mini App).
2. **Авторизация** — `resolve_role(uid)` ≠ None и `has_permission(role, perm)`
   для конкретного действия. Проверяется на сервере в каждом админ-API-роуте
   (middleware/guard), НЕ только в UI.
3. **Аудит** — каждое мутирующее действие пишет `audit_log`.
4. **Транспорт** — только HTTPS; админ-роуты под отдельным префиксом
   `/api/admin/*` с общим guard'ом.
5. **Принцип** — деньги/предметы меняет только бот-сторона логики
   (`change_balance` и т.п.) в транзакции БД; сайт лишь вызывает
   аутентифицированный write-API. Прямой записи в игровые таблицы из браузера
   нет.

---

## 4. Admin Panel — разделы и маршруты

Фронт — **проект** (без реализации). Все страницы под `/admin/*`, все API под
`/api/admin/*`. Гард: сессия + `resolve_role != None`; конкретные права — по
действию.

### Страницы (Next.js App Router, `v0-voznya`)
| Маршрут | Раздел | Право на вход | Содержимое |
|---------|--------|---------------|-----------|
| `/admin` | Dashboard | `dashboard.view` | сводка: онлайн, эмиссия ешек, последние действия из audit_log |
| `/admin/players` | Players | `players.view` | поиск игроков, профиль, баланс, роль, бан-статус |
| `/admin/players/[id]` | Player detail | `players.view` | карточка игрока, история транзакций, инвентарь, действия |
| `/admin/economy` | Economy | `economy.view` | выдать/снять ешки (нужно `economy.add/remove`), графики эмиссии |
| `/admin/inventory` | Inventory | `inventory.view` | предметы игроков, выдать/удалить (нужно `inventory.grant/revoke`) |
| `/admin/shop` | Shop | `shop.view` | каталог товаров; CRUD под `shop.manage` (будущий магазин) |
| `/admin/logs` | Logs | `logs.view` | лента audit_log с фильтрами (актор, цель, action, дата) |
| `/admin/moderation` | Moderation | `moderation.view` | очередь жалоб, бан/разбан (нужно `moderation.ban`) |

### API (route handlers)
| Метод + путь | Право | Действие | Пишет audit_log |
|--------------|-------|----------|:---------------:|
| `GET /api/admin/me` | (любая роль) | вернуть роль + список прав текущего пользователя | — |
| `GET /api/admin/dashboard` | `dashboard.view` | агрегаты | — |
| `GET /api/admin/players` | `players.view` | список/поиск | — |
| `GET /api/admin/players/[id]` | `players.view` | детали игрока | — |
| `PATCH /api/admin/players/[id]` | `players.edit` | правка профиля/заметок | ✅ |
| `POST /api/admin/economy/grant` | `economy.add` | начислить ешки | ✅ (`economy.add` + transaction) |
| `POST /api/admin/economy/revoke` | `economy.remove` | списать ешки | ✅ (`economy.remove` + transaction) |
| `GET /api/admin/inventory/[userId]` | `inventory.view` | инвентарь игрока | — |
| `POST /api/admin/inventory/grant` | `inventory.grant` | выдать предмет | ✅ |
| `POST /api/admin/inventory/revoke` | `inventory.revoke` | удалить предмет | ✅ |
| `GET/POST/PATCH/DELETE /api/admin/shop/products` | `shop.view`/`shop.manage` | каталог | ✅ (мутации) |
| `GET /api/admin/logs` | `logs.view` | чтение audit_log с фильтрами | — |
| `POST /api/admin/moderation/ban` | `moderation.ban` | бан | ✅ (`player.ban`) |
| `POST /api/admin/moderation/unban` | `moderation.ban` | разбан | ✅ (`player.unban`) |
| `POST /api/admin/roles` | `roles.manage` | назначить/изменить роль | ✅ (`role.change`) |

Все мутации пишут БД и `audit_log` в одной транзакции. Денежные — ещё и
`transactions`.

---

## 5. Transaction system — единая история операций

Единый леджер уже существует: таблица `transactions` (`app/models/transaction.py`).
Расширять схему НЕ требуется — поле `reason: VARCHAR(32)` уже принимает любой
тип, `meta: JSONB` хранит детали. Нужно лишь **зафиксировать канонический набор
типов**, чтобы код и админка не расходились.

### Канонические типы (`transactions.reason`)
| reason | Когда | Знак amount | Источник |
|--------|-------|:-----------:|----------|
| `reward` | награды (ферма, клад, событие-награда) | + | бот |
| `purchase` | покупка в магазине | − | магазин (будущее) |
| `gift` | подарок между игроками | ± | будущее (вне скоупа) |
| `admin_add` | начисление администратором | + | админ-панель/бот |
| `admin_remove` | списание администратором | − | админ-панель/бот |
| `duel` | исход дуэли | ± | бот |
| `family` | семейные операции | ± | бот |
| `event` | игровые события/розыгрыши | ± | бот |

> Текущий код уже пишет разные `reason` (farm/casino/duel/treasure/marriage/…).
> Рекомендация: при реализации админки и магазина приводить новые операции к
> этому набору верхнего уровня, а под-причину держать в `meta` (например
> `reason="reward", meta={"source":"farm"}`). Старые значения не ломаем —
> миграция данных не требуется, набор расширяемый.

### Структура `transactions` (как есть, реализовано)
```
┌────────────┬─────────────┬──────────┬───────────────────────────────┐
│ id         │ BIGINT      │ NOT NULL │ PK, autoincrement             │
│ user_id    │ BIGINT      │ NOT NULL │ INDEX                         │
│ amount     │ BIGINT      │ NOT NULL │ + начисление / − списание     │
│ reason     │ VARCHAR(32) │ NOT NULL │ канонический тип (см. выше)   │
│ meta       │ JSONB       │ NULL     │ детали (source, multiplier…)  │
│ created_at │ TIMESTAMPTZ │ NOT NULL │ DEFAULT now()                 │
└────────────┴─────────────┴──────────┴───────────────────────────────┘
Индексы: ix_transactions_user_id, ix_transactions_user_reason
```

---

## 6. Inventory foundation — структура (без реализации предметов)

**Только проект.** Без кода, без миграций, без каталога предметов. Две таблицы;
согласованы со схемой магазина из `FOUNDATION_STATUS.md` §5 (там `inventory_items`
и `purchase_history` — это те же сущности под чуть иными именами; при реализации
свести к одному набору имён).

```
inventory (что у игрока есть сейчас)
┌──────────────┬──────────────┬──────────┬─────────────────────────────────┐
│ id           │ BIGINT       │ PK       │ autoincrement                   │
│ user_id      │ BIGINT       │ NOT NULL │ владелец (users.user_id)        │
│ item_code    │ VARCHAR(64)  │ NOT NULL │ машинный код предмета           │
│ quantity     │ INTEGER      │ NOT NULL │ DEFAULT 1, CHECK (quantity >= 0)│
│ payload      │ JSONB        │ NULL     │ состояние предмета              │
│ equipped     │ BOOLEAN      │ NOT NULL │ DEFAULT false (косметика)       │
│ source       │ VARCHAR(16)  │ NOT NULL │ 'purchase'|'admin'|'reward'     │
│ acquired_at  │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now()                   │
└──────────────┴──────────────┴──────────┴─────────────────────────────────┘
  UNIQUE (user_id, item_code)   — стакаем количество, не плодим строки
  INDEX (user_id)

inventory_history (append-only движения предметов)
┌──────────────┬──────────────┬──────────┬─────────────────────────────────┐
│ id           │ BIGINT       │ PK       │ autoincrement                   │
│ user_id      │ BIGINT       │ NOT NULL │                                 │
│ item_code    │ VARCHAR(64)  │ NOT NULL │ снимок кода                     │
│ delta        │ INTEGER      │ NOT NULL │ +выдано / −снято                │
│ event        │ VARCHAR(16)  │ NOT NULL │ 'grant'|'revoke'|'purchase'|'use'│
│ actor_user_id│ BIGINT       │ NULL     │ кто инициировал (админ/система) │
│ meta         │ JSONB        │ NULL     │                                 │
│ created_at   │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now()                   │
└──────────────┴──────────────┴──────────┴─────────────────────────────────┘
  INDEX (user_id, created_at)
```

Связи: `users (1) ──< inventory`, `users (1) ──< inventory_history`.
Каждое изменение `inventory` пишет строку в `inventory_history` (как
`transactions` для денег). Админ-выдача дополнительно пишет `audit_log`.

---

## 7. Проверка совместимости

| С чем | Совместимо? | Детали |
|-------|:-----------:|--------|
| Текущий `users` | ✅ | Новые таблицы ссылаются на `user_id` без FK (как `transactions`). Поля `users` не меняются. Бан-флаг при реализации модерации — добавится отдельной колонкой/таблицей, не ломая существующее. |
| OIDC | ✅ | Роль резолвится по реальному `user_id`. OIDC `sub` через `account_links` уже даёт `user_id` — админ-проверка работает поверх, ничего в OIDC не меняется. |
| `account_links` | ✅ | Не затрагивается. Админ может войти любым способом (OIDC/Login Widget/Mini App) — важен лишь итоговый `user_id`. |
| Mini App план | ✅ | Mini App-сессия несёт тот же `uid`. Админка доступна и из Mini App: те же `/api/admin/*` + Bearer-токен из `MINI_APP_PLAN.md` §8. `getSession` (с Bearer) → `resolve_role` → права. |
| Транзакции | ✅ | Используем существующий леджер; типы расширяемы без миграции. |
| Бот | ✅ | `permissions.py` шарится: админ-команды бота проверяют `has_permission` той же логикой, что и сайт. |

Вывод: фундамент кладётся поверх текущей архитектуры без переделок. Ни OIDC, ни
account_links, ни Mini App-план не меняются.

---

## 8. ER-схема

```
                         ┌─────────────┐
                         │   users     │  (PK user_id)
                         └──────┬──────┘
            ┌───────────────────┼───────────────────────┬───────────────┐
            │                   │                        │               │
       (user_id)            (user_id)                (user_id)       (user_id)
            │                   │                        │               │
   ┌────────▼──────┐   ┌────────▼────────┐      ┌────────▼──────┐  ┌─────▼────────┐
   │ admin_roles   │   │  transactions   │      │  inventory    │  │ account_links │
   │ (PK user_id)  │   │ (ledger валюты) │      │ (план)        │  │ sub ↔ user_id │
   └───────────────┘   └─────────────────┘      └───────┬───────┘  └──────────────┘
                                                         │
                                                 ┌───────▼──────────┐
                                                 │ inventory_history│ (план)
                                                 └──────────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │ audit_log  (actor_user_id, target_user_id → users; без FK)    │
   │ кто-что-над-кем; контекст для economy/inventory/role/ban      │
   └──────────────────────────────────────────────────────────────┘

   Будущее (FOUNDATION_STATUS.md §5): shop_products, shop_orders,
   purchase_history — связаны с users и transactions.

   Связи логические (по user_id), физических FK нет — конвенция проекта.
```

---

## 9. Список таблиц

| Таблица | Статус | Назначение |
|---------|--------|-----------|
| `admin_roles` | ✅ реализована | роль игрока на платформе |
| `audit_log` | ✅ реализована | лента админ-действий |
| `transactions` | ✅ существует | единый леджер валюты (типы зафиксированы в §5) |
| `inventory` | 📋 проект | предметы игрока |
| `inventory_history` | 📋 проект | движения предметов |
| `shop_products` / `shop_orders` / `purchase_history` | 📋 проект | магазин (см. `FOUNDATION_STATUS.md`) |
| `users`, `account_links`, `oidc_link_requests` | ✅ существуют | не меняются |

---

## 10. Список API (сводка)
Все под `/api/admin/*`, гард = сессия + право. Полная таблица — §4. Мутации
пишут `audit_log` (+ `transactions` для денег, + `inventory_history` для
предметов).

Ключевые: `GET /api/admin/me`, `players`, `economy/grant|revoke`,
`inventory/grant|revoke`, `logs`, `moderation/ban|unban`, `roles`.

---

## 11. Список страниц
`/admin` · `/admin/players` · `/admin/players/[id]` · `/admin/economy` ·
`/admin/inventory` · `/admin/shop` · `/admin/logs` · `/admin/moderation`.

---

## 12. Roadmap реализации

**Этап A — фундамент (этот проход, готово в коде):**
- [x] `admin_roles` + `audit_log` (модели + миграция 0008)
- [x] `permissions.py` (роли → права, единый источник)

**Этап B — бот-сторона ролей и аудита:**
- [ ] репозиторий `admin_roles` (get/set/list роль) + `resolve_role` с
  bootstrap из `ADMIN_IDS`
- [ ] сервис записи `audit_log` (хелпер `write_audit(...)`)
- [ ] перевести существующие админ-команды бота на `has_permission` + аудит
- [ ] (опц.) команды `/grant`, `/revoke`, `/setrole` с записью в обе таблицы

**Этап C — серверная база админки (сайт):**
- [ ] `getSession` с Bearer (общее с Mini App, см. `MINI_APP_PLAN.md`)
- [ ] guard `/api/admin/*` (сессия → роль → право)
- [ ] портировать `permissions` в TS (или отдать через `GET /api/admin/me`)
- [ ] `GET /api/admin/me`, `players`, `economy/*`, `logs`

**Этап D — фронт админки:**
- [ ] layout `/admin` + навигация по разделам (скрывать по правам)
- [ ] Dashboard, Players, Economy, Logs (первые рабочие экраны)
- [ ] Moderation (бан/разбан)

**Этап E — инвентарь и магазин (после фундамента):**
- [ ] `inventory` + `inventory_history` (миграция)
- [ ] таблицы магазина (`FOUNDATION_STATUS.md` §5)
- [ ] разделы Inventory и Shop в панели

---

## 13. Что НЕ делалось (по требованию)
- ❌ магазин (только проект таблиц)
- ❌ реализация предметов инвентаря (только структура)
- ❌ фронт админки (только маршруты и API-контур)
- ❌ отдельная таблица `admin_sessions` (решено переиспользовать JWT, §3)
- ❌ отдельная таблица `admin_permissions` (права в коде, §3)
- ❌ подарки и новые игровые механики
