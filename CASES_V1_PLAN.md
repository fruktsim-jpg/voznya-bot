# CASES_V1_PLAN

План реализации системы кейсов «Возня» — версия V1.

Статус: **проект, не реализовано**. Этот документ описывает архитектуру и порядок
работ. Код и миграции по нему пишутся отдельными шагами после утверждения.

## Решения, принятые до плана (контекст)

- `inventory_instances` вводим **сейчас только как схему** (миграция 0016), без
  рантайма. Это страхует контракт под будущие Telegram Gifts / серийники.
- Cases V1 работают **только на текущем стековом `inventory`**.
- Все награды выдаются через **единую точку `grant_reward()`**.
- V1 поддерживает награды: **предметы каталога** и **ешки**. БЕЗ Telegram Gifts,
  БЕЗ Stars, БЕЗ коллекций. Архитектура обязана позволить добавить их позже
  **без миграции существующих кейсов**.
- Точка невозврата = первая выдача уникального предмета (Gift/серийник). До неё
  всё обратимо. V1 её не достигает.

## Конвенции проекта (соблюдаем)

- PostgreSQL + Alembic, цепочка миграций линейна, текущий HEAD `0015_user_mmr_projection`.
  Новая ревизия `0016_cases_foundation` (+ `inventory_instances`) поверх 0015.
- **Без FK** — связи логические по `code`/`id` (как в 0009–0011).
- Бот — единственный писатель gameplay-таблиц. Сайт — read-only + админ-роуты.
- Каждая мутация — в одной транзакции, с записью в `inventory_history` и (для
  админ-действий) в `audit_log`.
- Справочники типов/редкостей дублируются в `app/settings/inventory.py` и
  `v0-voznya/lib/inventory.ts` — держать в синхроне (как MMR-ранги).
- Деньги/ешки идут **только** через экономическое ядро
  (`app/core/economy_events.py` + `transactions`); кейсы их не дублируют.

---

## 1. Миграция 0016 — `0016_cases_foundation` (только схема)

`down_revision = "0015_user_mmr_projection"`. Аддитивная, ничего существующего
не меняет структурно. Создаёт 4 таблицы (`inventory_instances`,
`case_definitions`, `case_rewards`, `case_openings`) и добавляет аддитивные
необязательные поля в `inventory_items`.

### 1.1 Аддитивные поля каталога (безопасно, nullable / с дефолтом)

```
ALTER TABLE inventory_items ADD COLUMN stackable BOOLEAN NOT NULL DEFAULT true;
-- затем снять server_default, чтобы приложение задавало явно (как с transferable в 0011)
ALTER TABLE inventory_items ADD COLUMN collection_code VARCHAR(64) NULL;  -- задел под коллекции
ALTER TABLE inventory_items ADD COLUMN series_total   INTEGER     NULL;  -- задел под серийники
```

`stackable=false` пометит будущие per-instance предметы; в V1 все каталоговые
предметы остаются `stackable=true` и живут в `inventory`. `collection_code` и
`series_total` в V1 не используются (задел, чтобы не делать миграцию позже).

### 1.2 `inventory_instances` (только схема, рантайма нет)

Per-instance владение под будущие Gifts/серийники. В V1 **не пишется и не
читается** — таблица пустая.

```
inventory_instances
  id              BIGSERIAL PK
  user_id         BIGINT      NOT NULL
  item_code       VARCHAR(64) NOT NULL          -- → inventory_items.code (логически)
  instance_state  VARCHAR(16) NOT NULL          -- owned|pending|granted|failed|consumed
  serial_no       INTEGER     NULL              -- «#3 из 100»
  telegram_gift_id TEXT       NULL              -- id подарка в Telegram
  is_upgraded     BOOLEAN     NOT NULL DEFAULT false
  collection_code VARCHAR(64) NULL
  source          VARCHAR(16) NOT NULL          -- case|gift|admin|...
  payload         JSONB       NULL
  audit_id        BIGINT      NULL
  acquired_at     TIMESTAMPTZ NOT NULL DEFAULT now()
  INDEX ix_inv_instances_user (user_id)
  INDEX ix_inv_instances_item (item_code)
  PARTIAL UNIQUE uq_inv_instance_tg_gift (telegram_gift_id) WHERE telegram_gift_id IS NOT NULL
```

### 1.3 `case_definitions` — определение кейса

Кейс — это **предмет каталога** (`inventory_items` с `type='case'`); эта таблица
описывает его поведение при открытии.

```
case_definitions
  id              SERIAL PK
  item_code       VARCHAR(64) NOT NULL          -- UNIQUE → inventory_items.code (type='case')
  name            VARCHAR(128) NOT NULL
  description     TEXT NULL
  open_cost_kind  VARCHAR(16) NOT NULL          -- free|currency  (задел: stars — позже)
  open_cost_amount BIGINT     NOT NULL DEFAULT 0 -- ешки за открытие, если currency
  consumes_key    BOOLEAN     NOT NULL DEFAULT true  -- открытие списывает 1 кейс из inventory
  is_active       BOOLEAN     NOT NULL DEFAULT true
  season_code     VARCHAR(32) NULL              -- задел под сезоны
  starts_at       TIMESTAMPTZ NULL
  ends_at         TIMESTAMPTZ NULL
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  UNIQUE uq_case_definitions_item (item_code)
  INDEX ix_case_definitions_active (is_active)
```

Модель открытия: либо игрок владеет предметом-кейсом и тратит его
(`consumes_key=true`), либо открытие стоит ешки (`open_cost_kind='currency'`),
либо бесплатно (ежедневный кейс — `free`). Комбинируемо.

### 1.4 `case_rewards` — дроп-лист и веса

Одна строка = один возможный дроп. **`reward_kind` уже сейчас допускает будущие
значения** (`tg_gift`, `stars`), но V1-валидатор примет только `item`/`currency`.

```
case_rewards
  id               SERIAL PK
  case_item_code   VARCHAR(64) NOT NULL          -- → case_definitions.item_code
  reward_kind      VARCHAR(16) NOT NULL          -- V1: item|currency ; задел: tg_gift|stars
  reward_item_code VARCHAR(64) NULL              -- если kind=item → inventory_items.code
  amount           BIGINT      NULL              -- если kind=currency → ешки
  weight           INTEGER     NOT NULL          -- целочисленный вес; P = weight/Σweight
  min_qty          INTEGER     NOT NULL DEFAULT 1
  max_qty          INTEGER     NOT NULL DEFAULT 1
  max_global_supply INTEGER    NULL              -- лимит выпадений (джекпот); NULL = ∞
  granted_count    INTEGER     NOT NULL DEFAULT 0
  is_jackpot       BOOLEAN     NOT NULL DEFAULT false
  meta             JSONB       NULL
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
  INDEX ix_case_rewards_case (case_item_code)
  CHECK ck_case_rewards_weight  (weight > 0)
  CHECK ck_case_rewards_qty     (min_qty >= 1 AND max_qty >= min_qty)
  CHECK ck_case_rewards_kind    (
        (reward_kind='item'     AND reward_item_code IS NOT NULL)
     OR (reward_kind='currency' AND amount IS NOT NULL AND amount > 0)
     OR (reward_kind IN ('tg_gift','stars'))   -- разрешено схемой, запрещено V1-кодом
  )
  CHECK ck_case_rewards_supply  (max_global_supply IS NULL OR granted_count <= max_global_supply)
```

### 1.5 `case_openings` — леджер открытий (честность + аудит)

Append-only. Отдельно от `inventory_history`: здесь — «что выпало и почему»,
там — «движение активов».

```
case_openings
  id               BIGSERIAL PK
  user_id          BIGINT      NOT NULL
  case_item_code   VARCHAR(64) NOT NULL
  reward_id        INTEGER     NULL          -- → case_rewards.id (что выпало)
  reward_kind      VARCHAR(16) NOT NULL
  reward_item_code VARCHAR(64) NULL
  amount           BIGINT      NULL
  qty              INTEGER     NOT NULL DEFAULT 1
  roll             INTEGER     NOT NULL       -- выпавшее число [0, Σweight)
  weight_snapshot  JSONB       NOT NULL       -- слепок весов на момент открытия
  server_seed      VARCHAR(64) NULL           -- задел под provably-fair
  transaction_id   BIGINT      NULL           -- проводка стоимости открытия
  audit_id         BIGINT      NULL
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
  INDEX ix_case_openings_user (user_id, created_at)
  INDEX ix_case_openings_case (case_item_code, created_at)
```

`weight_snapshot` фиксирует дроп-лист на момент открытия — даже если позже
админ поменяет веса, прошлое открытие остаётся проверяемым.

---

## 2. `case_definitions` (модель + репозиторий)

- Модель `app/models/case_definition.py` (SQLAlchemy, как существующие модели).
- Репозиторий `app/repositories/cases.py`: `get_active_cases()`,
  `get_case_by_item_code(code)`, `get_case_rewards(case_item_code)`.
- Константа `CASE_TYPE = "case"` добавляется в справочник типов
  `app/settings/inventory.py` И в `v0-voznya/lib/inventory.ts` (TYPE_EMOJI: `🎁`/`📦`).

## 3. `case_rewards` (модель + валидация)

- Модель `app/models/case_reward.py`.
- Валидатор скоупа V1 (в сервисе, не в БД): при загрузке дроп-листа отвергаем
  строки с `reward_kind not in {item, currency}` с понятной ошибкой
  «kind не поддержан в V1». Схема их допускает — код запрещает. Это и есть
  «расширяемость без миграции»: включение Gifts/Stars = снятие ограничения в
  валидаторе + новая ветка в `grant_reward()`.

## 4. `case_openings` (леджер)

- Модель `app/models/case_opening.py`, репозиторий пишет строку в той же
  транзакции, что и выдача.
- Сайт (read-only) и админка читают леджер для просмотра честности/истории.

---

## 5. `grant_reward()` — единая точка выдачи (ядро расширяемости)

Расположение: `app/features/cases/rewards.py` (или `app/core/rewards.py`, если
захотим переиспользовать в shop). Сигнатура (черновик):

```python
async def grant_reward(
    session: AsyncSession,
    *,
    user_id: int,
    reward_kind: str,        # "item" | "currency"  (V1)
    reward_item_code: str | None,
    amount: int | None,
    qty: int,
    source: str,             # "case"
    actor_user_id: int | None,
    audit_id: int | None,
    transaction_id: int | None,
    meta: dict | None,
) -> RewardResult:
    ...
```

Тело — диспетчер по `reward_kind`. В V1 реализованы две ветки:

- **`currency`** → вызвать существующее экономическое ядро (начисление ешек +
  проводка в `transactions`, событие из `economy_events`). Кейсы НЕ пишут баланс
  напрямую.
- **`item`** → стековый upsert в `inventory` (как делает админ-роут
  `/api/admin/inventory`: `INSERT ... ON CONFLICT (user_id,item_code) DO UPDATE
  SET quantity = quantity + EXCLUDED.quantity`) + строка в `inventory_history`
  (`event='case'`, `source='case'`).

Заглушки на будущее (НЕ реализуются, но ветка обозначена явным `raise`):

- **`tg_gift`** → позже: создать `inventory_instances(state=pending)` + поставить
  задачу на `sendGift`. В V1 `raise NotImplementedError("tg_gift: post-V1")`.
- **`stars`** → позже: через `telegram_payments`. В V1 `raise NotImplementedError`.

Контракт: все вызовы выдачи в Cases V1 идут ТОЛЬКО через `grant_reward()`.
Добавление Gifts/Stars = новая ветка здесь, без изменения кейсов, профиля,
инвентаря и сайта.

### Алгоритм открытия (транзакция, провабли-фэйр-ready)

1. Загрузить активный кейс (`is_active`, окно `starts_at/ends_at`).
2. Проверить условие открытия: владеет кейсом (если `consumes_key`) и/или хватает
   ешек (если `currency`).
3. Загрузить дроп-лист `case_rewards` WHERE `case_item_code` AND доступен по
   `max_global_supply`. Посчитать `Σweight`.
4. `roll = secrets.randbelow(Σweight)` → выбрать награду накопительной суммой.
5. В ОДНОЙ транзакции:
   - списать кейс (`inventory_history`, `delta=-1`) и/или ешки (через ядро);
   - инкремент `case_rewards.granted_count` (для лимиток — с проверкой supply);
   - `grant_reward(...)` для выпавшего;
   - запись `case_openings` со `weight_snapshot` и `roll`.
6. Вернуть результат для рендера (что выпало, новый баланс/кол-во).

---

## 6. Админский CRUD кейсов

Переиспользуем существующую инфраструктуру (RBAC + audit + autocomplete уже есть).

- **Права** (`app/core/permissions.py` ↔ `lib/auth/admin-permissions.ts`,
  держать в синхроне): новый домен `cases.*` — `cases.view`, `cases.manage`
  (CRUD кейсов/дропов), `cases.grant` (выдать кейс игроку). По ролям:
  view→support, manage/grant→admin+.
- **Сайт-роуты** (`v0-voznya/app/api/admin/cases/`):
  - `GET /api/admin/cases` — список кейсов;
  - `POST /api/admin/cases` — создать/обновить определение;
  - `GET|POST /api/admin/cases/[code]/rewards` — дроп-лист (с весами);
  - переиспользуем готовый `ItemPicker` (autocomplete по каталогу) для
    `item_code` кейса и `reward_item_code`.
- **UI** (`v0-voznya/app/admin/cases/`): список кейсов, редактор дроп-листа с
  показом вероятностей (`weight/Σweight` в %), предпросмотр.
- **Выдача кейса игроку**: расширить существующую панель действий
  (`app/admin/players/[id]/actions.tsx`) карточкой «Кейсы» (выдать N кейсов) —
  это обычная item-выдача, уже поддержана.
- Каждое изменение — строка в `audit_log` (как mmr/reputation/inventory роуты).

---

## 7. Команды бота

Новый роутер `app/features/cases/handlers.py`, тексты в `app/settings/texts.py`,
кнопки в `app/core/keyboards.py`.

- **`/кейсы`** (`/cases`) — список доступных кейсов: название, стоимость
  открытия, сколько кейсов у игрока, кнопки «Открыть».
- **`/кейс <code|название>`** — карточка одного кейса: описание, дроп-лист с
  редкостями и шансами (по `weight`), стоимость, кнопка «Открыть».
- **`/открыть <code>`** (`/open`) — открыть кейс (или callback с кнопки).
  Выполняет алгоритм §5, показывает анимацию/результат: что выпало, с подсветкой
  редкости (эмодзи из `RARITY_STYLES`).
- Антиспам/идемпотентность: защита от двойного нажатия (debounce + проверка в
  транзакции), как в дуэлях/казино.
- Фильтр чатов через существующий `middlewares/chat_filter.py`.

## 8. Отображение кейсов на сайте (read-only)

- `v0-voznya/app/cases/page.tsx` — витрина активных кейсов (название, редкости
  дропов, шансы). Данные через `lib/queries.ts` (новый `getActiveCases()` +
  `getCaseRewards()`), стилизация редкостей через готовый `rarityStyle`.
- Профиль игрока: блок «Открыто кейсов: N» (из `case_openings`) — опционально в
  V1.
- Запросы строго read-only (конвенция: сайт не пишет gameplay).

## 9. Логи открытия

- `case_openings` — источник правды по дропам (честность).
- `inventory_history` (`event='case'`) — движение выданных предметов.
- `transactions` — движение ешек (стоимость открытия и денежные награды).
- Админка: страница «История открытий» (фильтр по игроку/кейсу) + просмотр
  `weight_snapshot` для разбора спорных дропов.

## 10. Интеграция с существующими системами

- **Инвентарь**: награды-предметы и сами кейсы живут в стековом `inventory`.
  `/inventory` и сайт-инвентарь показывают их без изменений (кейс — предмет
  `type='case'`). Счётчик предметов в профиле уже учитывает quantity.
- **Экономика (ешки)**: и стоимость открытия, и денежные награды идут через
  экономическое ядро + `transactions`. Кейсы не трогают баланс напрямую.
- **MMR**: V1 — без прямой связи. Задел: открытие/джекпот может начислять MMR
  через существующий `app/repositories/mmr.py` — добавляется позже как ещё одна
  ветка пост-обработки `case_openings`, без изменения схемы.
- **Достижения**: «открыл первый кейс», «выбил джекпот», «открыл N кейсов» —
  реализуются поверх событий открытия (хук после успешной транзакции), используя
  существующую систему достижений. Каталог достижений (Python ↔
  `lib/voznya-bot.ts`) держать в синхроне.
- **Репутация**: прямой связи в V1 нет. При желании — наградой кейса может быть
  событие репутации позже (новая ветка `grant_reward`/пост-хук), без миграции.

---

## Порядок реализации (после утверждения плана)

1. Миграция `0016_cases_foundation` (схема: `inventory_instances` пустая +
   `case_definitions`/`case_rewards`/`case_openings` + поля каталога). Применить
   на dev, проверить `alembic upgrade head` и `downgrade`.
2. Модели + репозитории + синхрон справочников типов (Python ↔ TS: `case`).
3. `grant_reward()` (ветки `item`/`currency`; `tg_gift`/`stars` — `NotImplemented`).
4. Сервис открытия (взвешенный бросок, транзакция, леджер) + юнит-тесты на
   распределение весов и идемпотентность (pytest — стандарт проекта).
5. Команды бота (`/кейсы`, `/кейс`, `/открыть`).
6. Права `cases.*` (Python ↔ TS) + админ-роуты + UI CRUD + выдача кейса.
7. Сайт: витрина кейсов (read-only).
8. Достижения, привязанные к открытиям; (опц.) MMR-награды.

## Что гарантирует расширяемость без миграции существующих кейсов

- `reward_kind` в `case_rewards` уже допускает `tg_gift`/`stars` на уровне схемы;
  включение = снятие ограничения в валидаторе + новая ветка в `grant_reward()`.
- `inventory_instances` уже существует (пустая) — Gifts/серийники не потребуют
  новой миграции владения.
- `collection_code`/`series_total`/`season_code` заложены заранее.
- Вся выдача спрятана за `grant_reward()` — точка расширения одна и известна.
