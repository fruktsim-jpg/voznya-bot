# CONTINUATION REPORT — «Возня» / Cases V1

Дата фиксации: 2026-06-06. Назначение: открыть один файл и сразу понять, где
остановились, что сделано, что дальше, и какие решения уже приняты и НЕ
переобсуждаются. Этот документ — точка возврата к проекту.

Связанные документы: `CASES_V1_PLAN.md` (детальный план + раздел web/Mini
App-first), `AGENTS.md` (правила репозитория), `PROJECT_STATE_REPORT.md`,
`*_FOUNDATION.md`.

---

## 1. Текущее состояние проекта

`voznya-bot` — Telegram-бот экосистемы «Возня»: Python 3.12 + aiogram,
PostgreSQL, миграции Alembic, Docker. Спутник `v0-voznya` — сайт + админка
(Next.js).

### Реализовано и работает (live)
farm, duel, treasure, casino, marriage/para, pidor, achievements,
economy/transactions, ratings, profile, help, reputation, MMR, OIDC-привязка,
admin-платформа (RBAC в боте + UI на сайте + audit), Combot import.

### Foundation-only (схема/доки есть, рантайма нет)
inventory (есть стековый рантайм на чтение + выдача через админку), shop, gifts,
cosmetics, Mini App. Не считать рабочими.

### Источник истины и роли (см. §3)
PostgreSQL — источник истины. Бот — единственный писатель `users` и
gameplay-таблиц. Сайт — read-only по `users` + запись только через явные
админ-роуты (`/api/admin/*`) и OIDC-привязку.

### Миграции (цепочка линейна)
- 0001–0008: ядро (users, transactions, cooldowns, номинации, браки, клады,
  account_links/OIDC, отложенные удаления и т.д.).
- 0009: inventory foundation (`inventory_items`, `inventory`, `inventory_history`).
- 0010: shop foundation (`shop_categories`, `shop_offers`, `purchase_history`).
- 0011: gift foundation (`gift_transactions`, `inventory_items.transferable`).
- 0012: (combot/прочее — см. файл миграции).
- 0013: reputation foundation (`reputation_entries`).
- 0014: mmr foundation (`mmr_entries`).
- 0015: `0015_user_mmr_projection` — денормализация `users.mmr`.
- **0016: `0016_cases_foundation`** — НОВАЯ (Cases V1, см. §2). НЕ применена на
  prod, локально не прогонялась.

### Актуальный HEAD
- Прод/основная цепочка ДО кейсов: `0015_user_mmr_projection`.
- После применения 0016 HEAD станет `0016_cases_foundation`
  (`down_revision = "0015_user_mmr_projection"`).

---

## 2. Cases V1

### Концепция
Кейс — это предмет каталога `inventory_items` с `type='case'`. Открытие списывает
кейс и/или ешки, бросает взвешенный жребий по дроп-листу и выдаёт награду
(предмет или ешки) через единую точку. Каждое открытие пишется в append-only
леджер `case_openings` (полностью воспроизводимо: `roll` + `weight_snapshot`).

### Завершённые этапы (1–6)

**Этап 1 — миграция** `migrations/versions/0016_cases_foundation.py`:
- таблицы: `inventory_instances` (пустая, задел), `case_definitions`,
  `case_rewards`, `case_openings`;
- колонки каталога: `inventory_items.stackable / collection_code / series_total`;
- без FK; CHECK на `case_rewards.reward_kind` допускает `item|currency|tg_gift|
  stars` (последние два — задел, код V1 их не принимает); полный `downgrade()`.

**Этап 2 — модели SQLAlchemy** (стиль проекта, enum'ы как module-tuples):
- `app/models/inventory_instance.py` (`InventoryInstance`, `INSTANCE_STATES`);
- `app/models/case_definition.py` (`CaseDefinition`, `CASE_COST_KINDS`);
- `app/models/case_reward.py` (`CaseReward`, `REWARD_KINDS_V1`, `REWARD_KINDS_ALL`);
- `app/models/case_opening.py` (`CaseOpening`);
- зарегистрированы в `app/models/__init__.py` (импорт + `__all__`);
- в `app/models/inventory_history.py` в `INVENTORY_SOURCES` добавлен `"case"`.

**Этап 3 — репозиторий** `app/repositories/cases.py`:
`get_active_cases`, `get_case_by_item_code`, `get_case_rewards`,
`get_available_rewards_for_update` (FOR UPDATE, отсекает исчерпанные лимиты),
`get_recent_openings`, `count_openings`.

**Этап 4 — выдача наград**:
- `app/services/inventory_grant.py`: `grant_item` (атомарный upsert в `inventory`
  + запись в `inventory_history`), `consume_item` (списание под FOR UPDATE),
  исключение `UnknownItem`;
- `app/features/cases/rewards.py`: `grant_reward()` — ЕДИНАЯ точка выдачи.
  Ветки V1: `currency` (через `change_balance` + `transactions`, reason
  `EVENT_REWARD`), `item` (через `grant_item`). `tg_gift`/`stars` →
  `NotImplementedError`. Возвращает `RewardResult`.

**Этап 5 — открытие (ядро надёжности)** `app/features/cases/service.py`:
`open_case()` — ЕДИНСТВЕННАЯ атомарная точка открытия. Порядок в одной
транзакции: проверка кейса (активность/окно) → списание ключа (`consume_item`,
FOR UPDATE) → списание ешек (`change_balance`, reason `EVENT_PURCHASE`, FOR
UPDATE) → выбор награды по весам среди доступных (FOR UPDATE на дроп-листе,
`secrets.randbelow`) → инкремент `granted_count` для лимиток → `grant_reward()`
→ запись `case_openings` (`roll` + `weight_snapshot`). Commit/rollback —
`DbSessionMiddleware`. `OpenResult` со статусами.
- `app/features/cases/events.py`: `emit_case_opened()` — хук под будущие
  достижения (открыто кейсов / редкие / легендарные / заработано). Сейчас
  только логирует; данные восстановимы из `case_openings`.

**Этап 6 — бот (тонкий клиент)** `app/features/cases/handlers.py`:
команды `/кейсы` (`/cases`), `/кейс <code>` (`/case`), `/открыть <code>`
(`/open`) + callback `case:open:<code>:<user_id>` (проверка владельца кнопки).
- `app/core/keyboards.py`: `case_open(case_item_code, user_id)`;
- `app/settings/texts.py`: блок «Кейсы» (CASES_*, CASE_*);
- роутер зарегистрирован в `app/features/__init__.py` (`cases_router`).

### Полный список созданных/изменённых файлов
Созданы:
- `migrations/versions/0016_cases_foundation.py`
- `app/models/inventory_instance.py`, `case_definition.py`, `case_reward.py`,
  `case_opening.py`
- `app/repositories/cases.py`
- `app/services/inventory_grant.py`
- `app/features/cases/__init__.py`, `rewards.py`, `service.py`, `events.py`,
  `handlers.py`

Изменены:
- `app/models/__init__.py` (импорты + `__all__`)
- `app/models/inventory_history.py` (`INVENTORY_SOURCES += "case"`)
- `app/core/keyboards.py` (`case_open`)
- `app/settings/texts.py` (тексты кейсов)
- `app/features/__init__.py` (регистрация роутера; ранее был повреждён вставкой
  текста ТЗ — восстановлен в валидный Python)
- `CASES_V1_PLAN.md` (добавлен раздел web/Mini App-first)

### Оставшиеся этапы (НЕ начаты)
- 7) Админка кейсов: права `cases.*` (Python `app/core/permissions.py` ↔ TS
  `lib/auth/admin-permissions.ts`), CRUD кейсов/дроп-листа, просмотр шансов,
  история открытий, выдача кейса игроку (через существующую item-выдачу).
- 8) Сайтовая витрина: содержимое, шансы, история, красивое открытие.
- Достижения поверх `emit_case_opened`; (опц.) MMR-награды.
- В `app/main.py` `BOT_COMMANDS` НЕ добавлены `/cases`/`/open` (сознательно, до
  согласования меню).

### Принятые решения по Cases V1 (НЕ пересматривать)
- Кейс = предмет каталога `type='case'`; дроп-лист в `case_rewards`; история в
  `case_openings`.
- Выдача ТОЛЬКО через `grant_reward()`; открытие ТОЛЬКО через `open_case()`.
- V1 награды: `item` + `currency`. Gifts/Stars/коллекции — задел, не в V1.
- Без FK; деньги через экономическое ядро; предметы через `inventory_history`;
  прямых изменений баланса нет.
- `case_openings` хранит `roll` + `weight_snapshot` → провабли-фэйр-ready.
- `inventory_instances` создаётся, но рантаймом в V1 НЕ используется.

---

## 3. Архитектурные решения (ОКОНЧАТЕЛЬНЫЕ, не переобсуждать)

- **Бот = игровое ядро** (процесс, владеющий БД; единственный писатель gameplay).
- **PostgreSQL = источник истины.**
- **Сайт и Mini App = основные UX-интерфейсы** (приоритет: 1 сайт, 2 Mini App).
- **Бот = дополнительный (третичный) интерфейс** — тонкий текстовый клиент +
  кнопка в Mini App.
- **`open_case()` и `grant_reward()` = единственные реализации логики** открытия
  и выдачи. Эти функции не зависят от aiogram (принимают `session` + `user_id`).
- **Никакой логики открытия кейсов в TypeScript.** TS только рендерит. Веб
  вызывает Python-flow; дублирующих реализаций не существует.
- **FastAPI пока НЕ внедряется.** Вопрос отдельного HTTP-API слоя ОТЛОЖЕН до
  активной разработки Mini App. До тех пор открытие доступно через бота
  (in-process вызов `open_case`).
- Когда дойдём до Mini App: вводится тонкий Python HTTP-слой (FastAPI в
  контейнере бота), у которого единственный путь открытия — `POST
  /api/cases/{code}/open` → `open_case()`. Сайт/Mini App — клиенты этого слоя.

---

## 4. Inventory roadmap (зафиксировано)

- Текущий inventory — **стековый**: `inventory` (одна строка = вид предмета у
  игрока + `quantity`), каталог в `inventory_items`, связь по `item_code`, без FK.
- **`inventory_instances` уже заложен** как фундамент per-instance владения
  (миграция 0016, модель есть).
- **Рантайм инстансов пока НЕ используется** — таблица создаётся пустой.
- **Telegram Gifts будут строиться поверх `inventory_instances`** (own/pending/
  granted/failed, `telegram_gift_id`, серийники). Это и есть «точка невозврата» —
  до первой выдачи уникального предмета всё обратимо.
- **Cases V1 работают через обычный стековый inventory** (награды-предметы и сами
  кейсы — стековые, `stackable=true`).

---

## 5. Mini App roadmap (без реализации)

Что потребуется в будущем (список, не задачи):
- **Авторизация**: Telegram `initData` (HMAC-валидация) → резолв в `user_id`;
  переиспользовать существующую OIDC-привязку как мост к аккаунту сайта.
- **Профили**: чтение карточки игрока (как на сайте `/profile`).
- **Инвентарь**: просмотр стекового инвентаря + (позже) per-instance предметов.
- **Кейсы**: витрина активных кейсов.
- **Открытия**: вызов общего open-flow (`POST /api/cases/{code}/open`), анимация
  результата.
- **История**: чтение `case_openings` (свои открытия, проверяемость).
- **Коллекции**: группировка предметов по `collection_code` (схема готова).
- **Gifts**: интеграция Telegram Gifts поверх `inventory_instances`.
- **Stars**: нативные платежи Telegram Stars (`open_cost_kind='stars'`).

Все пункты используют тот же Python backend; UI переиспользует React-компоненты
сайта.

---

## 6. Кейсы мира Возни (предложение, без реализации)

Дроп указан схематично; точные веса/предметы задаются в `case_rewards` при
наполнении. Редкость кейса — ориентир ценности.

### Стартовые
- **Кейс новичка** — common. Концепция: первый кейс, выдаётся на старте/за
  регистрацию. Дроп: мелкие ешки (часто), базовый титул/бейдж (редко).
- **Кейс бродяги** — common. Концепция: «уличный» лут за активность. Дроп:
  ешки (часто), простая рамка/бейдж бродяги (нечасто), мелкий джекпот ешками
  (редко).

### Лоровые
- **Кейс Радика** — rare. Лор: вечно что-то теряет/должен. Дроп: ешки «найденная
  заначка», титул «Должник Радика» (uncommon), редкий бейдж-мем (rare).
- **Кейс Конинга** — epic. Лор: вовремя выводит, расчётливый. Дроп: крупные ешки,
  титул «Вывел вовремя» (rare), эпик-рамка (epic), джекпот (very rare).
- **Аптечный кейс** — uncommon. Лор: ферма/аптека. Дроп: ешки-«передоз бюджета»,
  бейдж аптеки (uncommon), титул «Фармацевт» (rare).
- **Кейс Зволле** — rare. Лор: локация, «всё уже видело». Дроп: ешки под
  скамейкой, рамка Зволле (rare), коллекционный бейдж города (collection-задел).
- **Кейс Амстера** — epic. Лор: столичный лоск. Дроп: крупные ешки, эпик-титул,
  эпик-аватар/рамка, редкий джекпот.
- **Кейс 67** — legendary. Лор: мемное число, культовый. Дроп: преимущественно
  редкое+; легендарный титул/бейдж «67» (legendary, лимитированный
  `max_global_supply`), крупный джекпот.
- **Чемоданный кейс** — uncommon→rare. Лор: «потерянный чемодан» (как клад). Дроп:
  широкий разброс ешек, случайный предмет средней редкости, шанс на rare-предмет.

Замечание: легендарные/лимитированные награды задаются через
`case_rewards.max_global_supply` + `is_jackpot`; коллекционные — через
`collection_code` (схема готова, рантайм коллекций — позже).

---

## 7. Монетизация — дорожная карта (без реализации)

- **Cosmetics**: платные/наградные косметические предметы (титулы, рамки, бейджи,
  аватары) — уже есть типы в каталоге; экипировка через `inventory.equipped` +
  частичный уникальный индекс по слоту.
- **Collections**: наборы предметов (`collection_code`/`series_total`), бонусы за
  полный набор; витрина коллекций в Mini App.
- **Telegram Gifts**: выдача уникальных подарков поверх `inventory_instances`
  (state-машина pending→granted, `telegram_gift_id`). Новая ветка `grant_reward`.
- **Telegram Stars**: платное открытие/покупки (`open_cost_kind='stars'`,
  `reward_kind='stars'`); интеграция `telegram_payments`.
- **Seasonal Pass**: сезоны (`season_code`), сезонные кейсы/награды, прогресс;
  чтение прогресса в Mini App.

Все пункты расширяют существующую схему без миграции уже созданных кейсов.

---

## 8. Следующие шаги (порядок)

**P1 — завершить Cases V1**
1. Админка кейсов: права `cases.*` (Python ↔ TS в синхроне), CRUD кейсов и
   дроп-листа, просмотр шансов, история открытий, выдача кейса игроку.
2. Сайтовая витрина: список кейсов, содержимое, шансы, история, красивое
   открытие (через общий backend flow).
3. Прогон миграции 0016 + тесты в Docker; (опц.) первые кейсы из §6; (опц.)
   `/cases`/`/open` в `BOT_COMMANDS`.

**P2 — Mini App foundation + Cosmetics**
4. Тонкий Python HTTP-слой (FastAPI) с open-endpoint + auth-мост (initData/
   сессия) — вводится здесь, не раньше.
5. Mini App: авторизация, профиль, инвентарь, витрина/открытие кейсов.
6. Cosmetics: экипировка/витрина косметики.

**P3 — Collections + Gifts + Stars**
7. Collections (наборы/бонусы).
8. Telegram Gifts (поверх `inventory_instances`).
9. Telegram Stars (платежи).

---

## 9. Риски и техдолг

**Не проверено (нет локального Python/БД — только Docker):**
- импорт моделей и регистрация в `Base.metadata`;
- синтаксис/корректность миграции 0016;
- работа `open_case()` end-to-end (блокировки, откаты, веса).

**Прогнать в Docker до мержа:**
- `docker compose exec bot alembic upgrade head` (применить 0016) и проверить
  `alembic downgrade -1` (откат);
- импорт приложения (`python -m app.main` поднимается без ImportError);
- pytest на распределение весов `_pick_reward` и на атомарность открытия
  (тестов пока НЕТ — добавить, pytest — стандарт проекта).

**Требует миграций:**
- 0016 ещё не применена нигде (dev/prod). На prod применять отдельно.
- Будущее: Gifts/Stars/Collections миграций существующих кейсов НЕ требуют
  (заделы в схеме), но добавят новые revision поверх 0016.

**Требует ручной проверки:**
- UX-тексты кейсов (`texts.py`) — отсмотреть в чате.
- Порядок роутеров в `app/features/__init__.py` (cases_router добавлен после
  casino) — убедиться, что команды кейсов не перехватываются.
- Синхрон Python↔TS при добавлении прав `cases.*` (как MMR-ранги).
- `app/features/__init__.py` ранее повреждался вставкой текста — перепроверить,
  что файл валиден (на момент отчёта — восстановлен).

**Замечания по консистентности:**
- `reward_kind` допускает tg_gift/stars на уровне БД, но `open_case` фильтрует
  по `REWARD_KINDS_V1` — при наполнении дроп-листов в V1 НЕ заводить tg_gift/
  stars-награды (иначе строка будет молча отфильтрована из розыгрыша).
- `case_openings.reward_id` ссылается на `case_rewards.id` логически (без FK) —
  при удалении строки дроп-листа исторические открытия сохраняют снимок в
  `weight_snapshot`/полях, но `reward_id` может «повиснуть» — это ожидаемо.

---

Конец отчёта. После фиксации новые фичи не реализуются до отдельного решения.
