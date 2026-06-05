# FOUNDATION STATUS — Возня

Документ-итог по укреплению фундамента перед магазином и Mini App.
Цель: через месяц можно продолжить проект без повторного аудита.

Дата: 2026-06-06
Скоуп: только укрепление текущей архитектуры авторизации и подготовка модели
данных. **Магазин, подарки и новые игровые механики НЕ реализованы** — намеренно.

Репозитории:
- `voznya-bot` — Telegram-бот (Python, aiogram 3 + SQLAlchemy 2 async + Alembic).
  **Единственный владелец БД и записи в игровые таблицы.**
- `v0-voznya` — сайт (Next.js 16 App Router, `pg`, `jose`). Read-only по
  игровым таблицам; пишет ровно в одну auth-таблицу (`oidc_link_requests`).

---

## 0. Что изменено в этом проходе (changelog)

| Файл | Изменение |
|------|-----------|
| `app/models/account_link.py` | Добавлено `UNIQUE(user_id)` (`uq_account_links_user_id`); связь стала строгой биекцией. |
| `migrations/versions/0007_account_links_unique_user.py` | Новая миграция: дедуп дублей по `user_id`, удаление старого неуникального индекса, добавление UNIQUE. |
| `app/repositories/account_links.py` | Атомарный consume через `DELETE … RETURNING`; защита от угона (новый исход `CONFLICT`); добавлен `delete_expired_link_requests`. |
| `app/features/linking/handlers.py` | Обработка нового исхода `LinkResult.CONFLICT` (понятное сообщение, без перепривязки). |
| `app/services/link_maintenance.py` | **Новый**: фоновая чистка протухших `oidc_link_requests` (раз в час). |
| `app/main.py` | Регистрация чистки в `on_startup` (`setup_link_maintenance`). |

> ⚠️ **Не проверено локально:** на машине разработчика нет рабочего интерпретатора
> Python (только Windows Store-заглушка) и нет venv, поэтому `py_compile`,
> `alembic upgrade` и тесты **не запускались**. Перед деплоем выполнить шаги из
> раздела «Чек-лист применения».

---

## 1. Текущее состояние авторизации

### Архитектурный принцип
Бот — единственный источник правды о пользователях. Сайт игровые таблицы только
**читает**. Запись с сайта возможна ровно в одну таблицу — `oidc_link_requests`
(заявки на привязку). Это сознательно узкая поверхность атаки.

### Два потока входа, одна сессия
Сессия общая для обоих потоков — stateless JWT (HS256) в httpOnly-cookie
`voznya_session`, TTL 30 дней. Таблицы сессий нет: токен сам несёт `uid`
(= `users.user_id`).

**Поток A — классический Login Widget** (`/api/auth/telegram`, fallback):
1. Telegram-виджет редиректит с подписанным payload.
2. Сайт проверяет HMAC: `secret = SHA256(bot_token)`, сверяет `hash`
   timing-safe, проверяет свежесть `auth_date` (≤ 1 час).
3. `id` из payload — это НАСТОЯЩИЙ Telegram id ⇒ сразу выдаётся сессия и
   редирект на `/profile/{id}`.

**Поток B — Telegram OIDC** (`/api/auth/telegram/oidc/*`, основной):
1. `/start` генерирует `state`, `nonce`, PKCE `code_verifier` (S256), кладёт их
   в короткоживущие httpOnly-cookie (10 минут), редиректит на
   `oauth.telegram.org/auth`.
2. `/callback`:
   - сверяет `state` с cookie (CSRF);
   - меняет `code` на `id_token` (Authorization Code + PKCE, `client_secret_basic`);
   - проверяет подпись `id_token` по JWKS провайдера, `issuer`, `audience`
     (== `client_id`), `exp`/`iat` (через `jose`), и `nonce`;
   - нормализует `sub` (строка, ≤ 255, `[A-Za-z0-9_-]+`).
3. **Ключевой момент:** OIDC `sub` Telegram — это НЕ Telegram user_id. Это
   непрозрачный pairwise-идентификатор, привязанный к `client_id`, и он
   больше 2^53. Реальный `user_id` берётся из `account_links`:
   - **связь есть** → выдаётся та же сессия, что и в потоке A, редирект на профиль;
   - **связи нет** → создаётся одноразовая заявка в `oidc_link_requests`,
     пользователь идёт на `/link?token=…`, оттуда — в бота по deep-link
     `t.me/<bot>?start=link_<token>`. Сессия НЕ выдаётся, пока привязка не
     подтверждена в боте.

**Подтверждение привязки (бот)** — `/start link_<token>` в личке:
бот видит настоящий `message.from_user.id`, гасит токен и создаёт связь
`oidc_sub -> user_id`. Логика — `consume_link_request` (атомарно, см. §2).

### Итоговая схема `account_links` (после миграции 0007)

```
Таблица: account_links
┌──────────────┬──────────────────────────┬──────────┬───────────────────────────┐
│ Колонка      │ Тип                      │ NULL     │ Ограничения               │
├──────────────┼──────────────────────────┼──────────┼───────────────────────────┤
│ oidc_sub     │ VARCHAR(255)             │ NOT NULL │ PRIMARY KEY               │
│ user_id      │ BIGINT                   │ NOT NULL │ UNIQUE (uq_account_links_user_id) │
│ created_at   │ TIMESTAMPTZ              │ NOT NULL │ DEFAULT now()             │
└──────────────┴──────────────────────────┴──────────┴───────────────────────────┘

Индексы:
  account_links_pkey            PRIMARY KEY (oidc_sub)
  uq_account_links_user_id      UNIQUE (user_id)   ← добавлено в 0007

Инвариант: БИЕКЦИЯ oidc_sub ↔ user_id.
  • один oidc_sub → максимум один user_id   (через PK)
  • один user_id  → максимум один oidc_sub   (через UNIQUE)
```

Обратный поиск «sub по user_id» обслуживается уникальным индексом
`uq_account_links_user_id`, поэтому отдельный неуникальный
`ix_account_links_user_id` удалён (был избыточен).

---

## 2. Аудит ACCOUNT_LINKS и OIDC_LINK_REQUESTS

### Что проверено и что было не так

| Требование | До | После |
|------------|-----|-------|
| Один `oidc_sub` → один `user_id` | ✅ (PK по `oidc_sub`) | ✅ без изменений |
| Один `user_id` → один `oidc_sub` | ❌ только неуникальный индекс — **двойная привязка возможна** | ✅ `UNIQUE(user_id)` |
| Невозможна двойная привязка | ❌ `ON CONFLICT … DO UPDATE` тихо перепривязывал | ✅ исход `CONFLICT`, перепривязки нет |
| Нет захвата через повторное использование токена | ⚠️ `get`+`delete` не атомарны при гонке | ✅ `DELETE … RETURNING` (атомарно) |
| TTL работает | ✅ `expires_at` проверяется | ✅ без изменений |
| Протухшие не используются | ✅ | ✅ без изменений |
| Повторное использование токена невозможно | ⚠️ окно гонки | ✅ закрыто |
| Consume атомарен | ❌ | ✅ |
| Индекс по `expires_at` | ✅ `ix_oidc_link_requests_expires_at` (с 0006) | ✅ без изменений |
| Очистка старых записей | ❌ отсутствовала — таблица росла | ✅ фоновая задача раз в час |

### Как теперь работает consume (`app/repositories/account_links.py`)

1. **Атомарное гашение токена** — один SQL:
   ```sql
   DELETE FROM oidc_link_requests WHERE token = :t
   RETURNING oidc_sub, expires_at
   ```
   PostgreSQL блокирует строку на удаление, поэтому при двух параллельных
   `/start link_<token>` строку получит ровно один вызов. Второй увидит
   `NOT_FOUND`. Replay невозможен — токен исчезает в момент использования.

2. **TTL**: если `expires_at <= now()`, токен уже сожжён, но связь не создаётся
   (`EXPIRED`).

3. **Биекция перед вставкой**: читаем владельца `sub` и текущую привязку
   `user_id`.
   - та же пара уже есть → идемпотентный `LINKED` (юзер нажал Start дважды);
   - любая сторона занята другой → `CONFLICT`, **без перепривязки**;
   - обе свободны → вставка.

4. **Защита от гонки на вставке**: вставка в savepoint (`begin_nested`)
   с `ON CONFLICT (oidc_sub) DO NOTHING`; `IntegrityError` по
   `uq_account_links_user_id` или `rowcount == 0` → `CONFLICT`.

### Очистка протухших заявок
`app/services/link_maintenance.py` — задача APScheduler `interval` раз в 60 мин:
```python
DELETE FROM oidc_link_requests WHERE expires_at <= now()
```
Опирается на `ix_oidc_link_requests_expires_at`. TTL заявки — 15 минут (задаётся
на стороне сайта в `lib/auth/account-link.ts`), так что часовой интервал чистки
с запасом достаточен.

### Схема `oidc_link_requests` (без изменений, с 0006)
```
Таблица: oidc_link_requests
┌──────────────┬──────────────┬──────────┬───────────────┐
│ Колонка      │ Тип          │ NULL     │ Ограничения   │
├──────────────┼──────────────┼──────────┼───────────────┤
│ token        │ VARCHAR(64)  │ NOT NULL │ PRIMARY KEY   │
│ oidc_sub     │ VARCHAR(255) │ NOT NULL │               │
│ expires_at   │ TIMESTAMPTZ  │ NOT NULL │ INDEX         │
│ created_at   │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now() │
└──────────────┴──────────────┴──────────┴───────────────┘
Индексы:
  oidc_link_requests_pkey           PRIMARY KEY (token)
  ix_oidc_link_requests_expires_at  (expires_at)   ← для TTL-чистки и проверок
```

---

## 3. Аудит сессий и edge cases

Сессия — JWT в httpOnly-cookie `voznya_session`, `SameSite=Lax`,
`Secure` в проде, `path=/`, TTL 30 дней. Подпись HS256 ключом `AUTH_SECRET`.

| Сценарий | Поведение | Статус |
|----------|-----------|--------|
| Первый вход (OIDC, связи нет) | Заявка в `oidc_link_requests` → `/link` → бот → связь. Сессия не выдаётся до подтверждения. | ✅ корректно |
| Первый вход (Login Widget) | `id` уже настоящий → сессия сразу. | ✅ |
| Повторный вход (связь есть) | OIDC `sub` → `user_id` из `account_links` → сессия. | ✅ |
| Logout | `/api/auth/logout` (GET+POST) обнуляет cookie (`maxAge=0`). | ✅ |
| Протухшая сессия | `jwtVerify` бросает по `exp` → `verifySessionToken` отдаёт `null` → пользователь считается гостем. | ✅ |
| Повторная привязка | Если `sub`/`user_id` уже заняты → `CONFLICT`, перепривязки нет. Та же пара → идемпотентно. | ✅ (после изменений) |

### Известные edge cases и ограничения (осознанные)

1. **Отзыв сессии до истечения JWT невозможен.** Сессии stateless: logout
   чистит cookie только в текущем браузере. При утечке токена он валиден до
   `exp` (≤ 30 дней). Это нормально для текущего риск-профиля (нет платежей).
   **Перед магазином с реальными покупками** — пересмотреть (см. §6, точки роста).

2. **Ротация `AUTH_SECRET`** инвалидирует ВСЕ сессии разом (все JWT перестают
   проверяться). Это и есть «глобальный logout» при инциденте.

3. **Смена `user_id` после привязки** не предусмотрена UI бота — только админ
   вручную (DELETE строки в `account_links`). Это сознательно: защита от угона
   важнее удобства самостоятельной перепривязки.


4. **`/profile/{id}` показывает чужие профили** — данные публичные (балансы,
   рейтинги и так на сайте видны всем). Сессия не нужна для просмотра; она нужна
   только для будущих приватных действий (магазин, инвентарь).

5. **Гонка «два разных токена для одного `sub`»**: один и тот же сайт-аккаунт
   может за 15 минут создать несколько заявок (несколько входов). Первый
   дошедший до бота создаёт связь; остальные токены при дойти до бота дадут
   `CONFLICT` (sub занят) или `NOT_FOUND`/`EXPIRED`. Безопасно.

6. **Часы БД vs приложения**: TTL считается через `now()` БД при создании
   (`now() + interval`) и `now_utc()` приложения при проверке. Расхождение
   часов теоретически возможно, но и сайт, и бот, и БД — в одной инфраструктуре;
   риск пренебрежимо мал.

---

## 4. Security review — найденные риски и статус

| # | Риск | До | Митигировано чем | Статус |
|---|------|-----|------------------|--------|
| 1 | **Replay Login Widget** | Свежесть `auth_date` ≤ 1ч + timing-safe HMAC | без изменений | ✅ |
| 2 | **Replay OIDC** | `nonce` + `state` + PKCE + одноразовый `code` | без изменений | ✅ |
| 3 | **Повторное использование link-token** | окно гонки (`get`+`delete`) | `DELETE … RETURNING` | ✅ закрыто |
| 4 | **Привязка чужого аккаунта (угон)** | `DO UPDATE` тихо перепривязывал | `UNIQUE(user_id)` + исход `CONFLICT` | ✅ закрыто |
| 5 | **Race conditions при привязке** | без атомарности | savepoint + `ON CONFLICT` + `IntegrityError` | ✅ закрыто |
| 6 | **Open redirect** | все редиректы строятся на `new URL('/...', request.url)` или фиксированных путях; внешний ввод (`token`, `userId`) не попадает в host | — | ✅ риска нет |
| 7 | **Хранение `oidc_sub`** | строка ≤ 255, не приводится к числу, не используется как `user_id`; в JWT/логи не попадает | — | ✅ |
| 8 | **Подделка `id_token`** | проверка подписи по JWKS, `iss`/`aud`/`exp`/`nonce` | без изменений | ✅ |
| 9 | **Кража сессии (XSS/CSRF)** | httpOnly + Secure + SameSite=Lax | без изменений | ✅ базово |

### Подробности по ключевым пунктам

**Open redirect (риск 6).** Все редиректы в auth-роутах используют либо
константные пути (`/`, `/?auth=...`), либо `new URL('/profile/${userId}', request.url)`
и `new URL('/link?token=${linkToken}', request.url)`. Базой всегда служит
`request.url` (свой домен), а пользовательский ввод идёт только в path/query, не
в host. Подставить внешний домен нельзя. `token` дополнительно
`encodeURIComponent` на `/link`.

**Хранение `oidc_sub` (риск 7).** `sub` хранится как строка (он > 2^53), нигде
не парсится в `Number`, не кладётся в сессионный JWT и не логируется. В сессии
живёт только реальный `user_id`. Это исключает потерю точности и утечку
pairwise-идентификатора.

### Остаточные риски (приемлемы сейчас, пересмотреть перед платежами)
- Невозможность серверного отзыва конкретной сессии (см. §3.1).
- `ssl: { rejectUnauthorized: false }` в `lib/db.ts` на сайте — принимает любой
  серверный сертификат managed-БД. Для платежей включить нормальную проверку CA.
- Нет rate-limit на auth-роутах сайта (бот прикрыт `AntiFloodMiddleware`).
  Для входа это не критично (всё подписано), но перед магазином стоит добавить.

---

## 5. Подготовка модели данных (магазин) — ТОЛЬКО схема

> **Без миграций, без кода, без UI.** Это проект таблиц для будущей реализации.
> Решения здесь — рекомендации, не финал.

### Принципы
- Магазин — игровая механика ⇒ владелец таблиц **бот**, не сайт. Сайт/Mini App
  читают каталог и историю, а действия-покупки идут через бота или
  аутентифицированный API, который пишет в БД от имени бота.
- Деньги (ешки) уже учитываются в `users.balance` + леджер `transactions`.
  Магазин должен списывать через существующий `change_balance`
  (`app/services/economy.py`), чтобы каждая покупка попадала в леджер
  (`reason="shop"`). Это сохраняет инвариант «всё движение валюты — в transactions».
- Все суммы — `BIGINT` (ешки неделимы, как в текущих моделях).

### Таблицы и связи

```
shop_products (каталог — что продаётся)
┌────────────────┬──────────────┬──────────┬─────────────────────────────────┐
│ id             │ BIGINT       │ PK       │ autoincrement                   │
│ code           │ VARCHAR(64)  │ UNIQUE   │ стабильный машинный ключ        │
│ title          │ VARCHAR(128) │ NOT NULL │                                 │
│ description     │ TEXT        │ NULL     │                                 │
│ price          │ BIGINT       │ NOT NULL │ цена в ешках, CHECK (price >= 0)│
│ kind           │ VARCHAR(32)  │ NOT NULL │ 'role' | 'cosmetic' | 'consumable' | ... │
│ payload        │ JSONB        │ NULL     │ что выдать (роль, предмет и т.п.)│
│ is_active      │ BOOLEAN      │ NOT NULL │ DEFAULT true (витрина)          │
│ stock          │ INTEGER      │ NULL     │ NULL = безлимит, иначе остаток  │
│ created_at     │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now()                   │
└────────────────┴──────────────┴──────────┴─────────────────────────────────┘
  INDEX (is_active, kind)   — выборка активной витрины по категориям

shop_orders (факт покупки — атомарная операция списания)
┌────────────────┬──────────────┬──────────┬─────────────────────────────────┐
│ id             │ BIGINT       │ PK       │ autoincrement                   │
│ user_id        │ BIGINT       │ NOT NULL │ FK → users.user_id              │
│ product_id     │ BIGINT       │ NOT NULL │ FK → shop_products.id           │
│ price_paid     │ BIGINT       │ NOT NULL │ цена на момент покупки (снимок) │
│ quantity       │ INTEGER      │ NOT NULL │ DEFAULT 1, CHECK (quantity > 0) │
│ status         │ VARCHAR(16)  │ NOT NULL │ 'completed'|'refunded'|'pending'│
│ transaction_id │ BIGINT       │ NULL     │ FK → transactions.id (списание) │
│ idempotency_key│ VARCHAR(64)  │ UNIQUE   │ защита от двойного списания     │
│ created_at     │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now()                   │
└────────────────┴──────────────┴──────────┴─────────────────────────────────┘
  INDEX (user_id, created_at)   — история заказов пользователя

inventory_items (что у игрока есть после покупок/наград)
┌────────────────┬──────────────┬──────────┬─────────────────────────────────┐
│ id             │ BIGINT       │ PK       │ autoincrement                   │
│ user_id        │ BIGINT       │ NOT NULL │ FK → users.user_id              │
│ product_id     │ BIGINT       │ NULL     │ FK → shop_products.id (источник)│
│ kind           │ VARCHAR(32)  │ NOT NULL │ дублирует тип для быстрых выборок│
│ quantity       │ INTEGER      │ NOT NULL │ DEFAULT 1, CHECK (quantity >= 0)│
│ payload        │ JSONB        │ NULL     │ состояние предмета              │
│ equipped       │ BOOLEAN      │ NOT NULL │ DEFAULT false (для косметики)   │
│ acquired_at    │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now()                   │
└────────────────┴──────────────┴──────────┴─────────────────────────────────┘
  UNIQUE (user_id, product_id) WHERE product_id IS NOT NULL  — стакать кол-во, а не плодить строки
  INDEX (user_id, kind)

purchase_history (аудит-леджер магазина — append-only)
┌────────────────┬──────────────┬──────────┬─────────────────────────────────┐
│ id             │ BIGINT       │ PK       │ autoincrement                   │
│ user_id        │ BIGINT       │ NOT NULL │ FK → users.user_id              │
│ order_id       │ BIGINT       │ NULL     │ FK → shop_orders.id             │
│ product_code   │ VARCHAR(64)  │ NOT NULL │ снимок кода (товар могут удалить)│
│ product_title  │ VARCHAR(128) │ NOT NULL │ снимок названия                 │
│ price_paid     │ BIGINT       │ NOT NULL │ снимок цены                     │
│ event          │ VARCHAR(16)  │ NOT NULL │ 'purchase'|'refund'|'grant'     │
│ meta           │ JSONB        │ NULL     │                                 │
│ created_at     │ TIMESTAMPTZ  │ NOT NULL │ DEFAULT now()                   │
└────────────────┴──────────────┴──────────┴─────────────────────────────────┘
  INDEX (user_id, created_at)
```

### Связи (ER)
```
users (1) ──< shop_orders >── (1) shop_products
users (1) ──< inventory_items >── (0..1) shop_products
users (1) ──< purchase_history
shop_orders (1) ──< purchase_history
shop_orders (0..1) ── transactions   (списание ешек уходит в общий леджер)
```

### Целостность покупки (для будущей реализации)
- Покупка = одна транзакция БД: блокировка строки `users` (`SELECT … FOR UPDATE`,
  как в `claim_treasure`), проверка `balance >= price`, `change_balance(-price,
  reason="shop")`, `INSERT shop_orders`, upsert `inventory_items`,
  `INSERT purchase_history`.
- `idempotency_key` в `shop_orders` (UNIQUE) защищает от двойного клика/ретрая.
- `price_paid` и снимки в `purchase_history` хранятся отдельно от каталога:
  товар можно деактивировать/переименовать без искажения истории.

### Сознательно НЕ включено
Подарки между игроками (`gifts`), скидки/промокоды, корзина из нескольких
позиций. Это отдельная механика — вне текущего скоупа.

---

## 6. Mini App readiness

### Что уже готово (переиспользуется как есть)
- **Проверка `initData`** Mini App уже написана: `verifyWebAppInitData` в
  `v0-voznya/lib/auth/telegram.ts`. Алгоритм по докам Telegram WebApp
  (`secret = HMAC_SHA256("WebAppData", bot_token)`), timing-safe сравнение,
  свежесть `auth_date`. **Пока не подключена к роуту.**
- **Сессионный слой** (`lib/auth/session.ts`, `get-session.ts`) полностью
  переиспользуется: тот же JWT-cookie, тот же `SessionPayload { uid }`.
  Mini App после проверки `initData` выдаёт точно такую же сессию.
- **Read-only API** уже есть и готово к показу в Mini App: `/api/me`,
  `/api/me/summary`, `/api/profile/[id]`, `/api/economy`, `/api/top-rich`,
  `/api/top-weekly`, `/api/achievements`, `/api/daily`, `/api/families`,
  `/api/messages`, `/api/stats`, `/api/commands`.
- **UI-компоненты** под `v0-voznya/components/` (Radix + Tailwind 4 +
  framer-motion): карточки профиля, балансы, рейтинги, графики (recharts),
  тосты (sonner), дровер (vaul). Витрина магазина и инвентарь собираются из них
  без новых зависимостей.
- **Слой запросов** `lib/queries.ts` — готовый набор read-only выборок
  (`getPlayerProfile`, `getUserSummary`, `getTopRich`, …).
- **Привязка `oidc_sub → user_id`** — переиспользуется. В контексте Mini App,
  впрочем, `initData.user.id` уже является настоящим Telegram id, поэтому
  привязка для Mini App-входа НЕ нужна (см. ниже).

### Что придётся изменить / добавить
1. **Новый роут** `POST /api/auth/telegram-webapp`: принимает `initData`,
   вызывает `verifyWebAppInitData`, выдаёт стандартную сессию. ~30 строк, по
   образцу `app/api/auth/telegram/route.ts`. Привязка не требуется — `id` из
   `initData` уже настоящий.
2. **Загрузка Telegram WebApp SDK** на клиенте (сейчас зависимости нет):
   либо подключить `telegram-web-app.js` скриптом, либо добавить
   `@twa-dev/sdk`. Нужно для `initDataRaw`, `themeParams`, `viewport`,
   `MainButton`.
3. **Тема и вьюпорт**: текущая вёрстка под обычный сайт; для Mini App учесть
   `themeParams`, `safe area`, фиксированную высоту, отсутствие скролла body.
4. **`SameSite` cookie в iframe Telegram**: Mini App работает во встроенном
   webview/iframe. `SameSite=Lax` может не отдать cookie в кросс-контексте —
   возможно, понадобится `SameSite=None; Secure` для Mini App-сессии (проверить
   на устройстве; не менять глобально, ввести отдельную ветку cookie при нужде).
5. **Магазин-действия** требуют write-API (см. §5) — единственное, чего сейчас
   нет вообще, т.к. сайт намеренно read-only.

### Что переиспользовать целиком
`session.ts`, `get-session.ts`, `verifyWebAppInitData`, весь `lib/queries.ts`,
все read-only API-роуты, компоненты `components/`. По сути для Mini App-входа
нужен только новый тонкий роут (п.1) и клиентский SDK (п.2).

---

## 7. Сводка: таблицы, миграции, ограничения, точки роста

### Все таблицы (актуальные)
| Таблица | Владелец | Назначение |
|---------|----------|------------|
| `users` | бот | игроки, экономика, статистика (PK `user_id`) |
| `transactions` | бот | леджер движения ешек |
| `cooldowns` | бот | кулдауны действий |
| `daily_nominations` | бот | Пидор/Пара дня |
| `marriages` | бот | браки |
| `pending_actions` | бот | отложенные действия (дуэли и т.п.) |
| `treasures` | бот | клады |
| `scheduled_deletions` | бот | отложенное удаление сообщений |
| `user_achievements` | бот | достижения |
| `message_daily` | бот | суточная активность сообщений |
| `account_links` | бот (пишет), сайт (читает) | биекция `oidc_sub ↔ user_id` |
| `oidc_link_requests` | сайт (пишет/читает), бот (consume) | одноразовые заявки привязки (TTL) |
| *(план)* `shop_products`, `shop_orders`, `inventory_items`, `purchase_history` | бот | §5, ещё не созданы |

### Миграции (Alembic, линейная цепочка)
```
0001_initial            базовая схема (users, transactions, …)
0002_achievements       достижения
0003_loss_counters      счётчики проигрышей
0004_message_stats      статистика сообщений
0005_open_duels         открытые дуэли
0006_account_links      account_links + oidc_link_requests
0007_account_links_unique_user   ← НОВАЯ: UNIQUE(user_id), дедуп, чистка индекса
```
Голова: `0007_account_links_unique_user` (down_revision `0006_account_links`).

### Ключевые ограничения БД (инварианты)
- `account_links`: PK `oidc_sub` + `UNIQUE(user_id)` ⇒ строгая биекция.
- `oidc_link_requests`: PK `token` (одноразовость) + индекс `expires_at` (TTL/чистка).
- `users`: PK `user_id` (Telegram id, не autoincrement).
- `transactions`: единый леджер, все движения валюты только через него.

### Будущие точки расширения
1. **Магазин** — создать 4 таблицы из §5 миграцией `0008_shop`; покупки через
   `change_balance(reason="shop")` в одной транзакции с блокировкой строки
   `users`; идемпотентность через `shop_orders.idempotency_key`.
2. **Mini App** — добавить `POST /api/auth/telegram-webapp` (§6, п.1) и
   клиентский WebApp SDK; сессионный слой не трогать.
3. **Отзыв сессий** — если появятся реальные платежи, рассмотреть
   server-side хранилище отзыва (короткий `jti`-blacklist в Redis) или
   укоротить TTL JWT. Сейчас не требуется.
4. **Rate-limit на auth-роутах сайта** — перед магазином.
5. **SSL CA-проверка** в `v0-voznya/lib/db.ts` — включить перед платежами.
6. **Подарки** — отдельная механика (таблица `gifts`), сознательно отложена.

### Чек-лист применения (выполнить в рабочем окружении с Python)
```bash
# 1. Поднять окружение бота (venv с зависимостями из requirements.txt)
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

# 2. Проверить, что код компилируется
python -m py_compile app/models/account_link.py app/repositories/account_links.py \
  app/services/link_maintenance.py app/main.py \
  migrations/versions/0007_account_links_unique_user.py

# 3. Применить миграцию (ОБЯЗАТЕЛЬНО на копии/проде с бэкапом)
alembic upgrade head

# 4. Sanity: убедиться, что UNIQUE создан
#    psql> \d account_links   → должен быть uq_account_links_user_id
```
> Миграция 0007 удаляет дубли по `user_id` (оставляя самую раннюю связь). На
> проде дублей быть не должно, но **снять бэкап перед `upgrade` обязательно** —
> `DELETE` необратим.

---

## Что НЕ делалось (по требованию задачи)
- ❌ магазин (только схема таблиц, §5)
- ❌ подарки
- ❌ новые игровые механики
- ❌ UI Mini App (только оценка готовности, §6)
- ❌ миграции для магазина (только проект схемы)
