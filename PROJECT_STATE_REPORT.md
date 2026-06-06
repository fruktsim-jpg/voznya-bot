# PROJECT STATE REPORT — Возня

Дата аудита: 2026-06-06. Метод: чтение кода и репозиториев `voznya-bot` (бот,
Python/aiogram) и `v0-voznya` (сайт, Next.js). Выводы подтверждены ссылками на
файлы. Ничего не реализовано, не отрефакторено и не удалено в рамках аудита.

> Честный принцип отчёта: «foundation» = есть модель + миграция (+ док), но НЕТ
> живых хендлеров/UI/API, которые это используют. «Работает» = есть код,
> вызываемый в проде (зарегистрированный роутер бота или страница/роут сайта).

---

## ЭТАП 1. Инвентаризация систем

Источник истины по боту — `app/main.py` (`create_dispatcher` подключает
`get_feature_routers()`) и `app/features/__init__.py` (строки ~40-67 регистрируют
17 роутеров: linking, welcome, reputation, farm, casino, duel, treasure, pidor,
para, marriage, profile, balance, ratings, achievements, mmr, help, admin).

| Система | Статус | Где |
|---|---|---|
| auth (Telegram Login Widget + сессии) | реализована (сайт) | `v0-voznya/app/api/auth/telegram/route.ts`, `lib/auth/` |
| OIDC (привязка через Telegram OIDC) | реализована | `app/features/linking/handlers.py`, `v0-voznya/app/api/auth/telegram/oidc/*` |
| account_links | реализована | `app/models/account_link.py`, `app/repositories/account_links.py`, миграции 0006/0007 |
| Mini App (Telegram WebApp) | не начата | нет `initData`/`telegram-web-app` в `v0-voznya`; только `MINI_APP_PLAN.md` |
| admin platform (бот) | реализована | `app/features/admin/`, `app/core/permissions.py`, миграция 0008 |
| admin UI (сайт) | реализована | `v0-voznya/app/admin/*`, `app/api/admin/*` |
| inventory | foundation-only | `app/models/inventory.py` + миграция 0009; нет репозитория/хендлеров |
| inventory history | foundation-only | `app/models/inventory_history.py` + 0009; не пишется |
| shop | foundation-only | `app/models/shop_*.py` + миграция 0010; нет логики покупки |
| gifts | foundation-only | `app/models/gift_transaction.py` + миграция 0011; нет хендлеров |
| reputation | реализована | `app/features/reputation/` (роутер зарегистрирован), миграция 0013 |
| MMR | реализована (бот) | `app/features/mmr/` (роутер зарегистрирован) + хуки начисления, миграция 0014 |
| achievements | реализована | `app/features/achievements/` |
| farm | реализована | `app/features/farm/` |
| family / marriage | реализована | `app/features/marriage/`, `app/features/para/` |
| treasure | реализована | `app/features/treasure/` |
| economy / transactions | реализована | `app/core/economy_events.py`, transactions в миграции 0001 |
| Combot import | реализована (скрипт), частично на сайте | `scripts/import_combot_history.py`, `app/repositories/combot_stats.py`, миграция 0012 |
| profile | реализована (бот + сайт) | `app/features/profile/handlers.py`, `v0-voznya/components/profile/player-card.tsx` |
| localization | частично (RU-only, без i18n-слоя) | тексты захардкожены в `app/settings/texts.py` |
| cosmetics / titles / badges / frames | foundation-only (каталог) + заглушка на сайте | типы предметов в `app/models/inventory_item.py` (`title/badge/frame`); `cosmetics` placeholder в `lib/queries.ts`. Экипировки/выдачи нет |

Примечание: «titles» в смысле игровых званий за `total_earned` РАБОТАЮТ на сайте
(`player-card.tsx`, массив `titles`), но это не косметика из каталога предметов —
это вычисляемое звание. Косметические титулы (item type `title`) — foundation.

---

## ЭТАП 2. MD-документы

Чёткий водораздел: файлы от 2026-06-05 — одноразовые отладочные отчёты (в
основном про `/профиль`), почти все устарели. Файлы от 2026-06-06 —
структурированные FOUNDATION-доки, актуальны как описание заложенного фундамента.

| Документ | Статус | % реализации | Комментарии |
|---|---|---|---|
| README.md | актуален | 100% | Основная инструкция/команды бота |
| CHANGELOG.md | актуален | 100% | v1.3 эконом-ребаланс, синхронен с docs/ECONOMY.md |
| docs/ECONOMY.md | актуален | 100% | Числа из реального конфига |
| AGENTS.md | устарел | — | Описывает репо как «greenfield stub» — давно неправда |
| FOUNDATION_STATUS.md | актуален | 90% | Опорный итог укрепления auth/моделей |
| MMR_FOUNDATION.md | актуален | ~95% | Описывает реализованный MMR; роутер и хуки на месте |
| REPUTATION_FOUNDATION.md | актуален | ~90% | Система репутации описана как рабочая v1 |
| INVENTORY_FOUNDATION.md | актуален (как foundation) | 30% | Честно помечен foundation: только модели/миграция |
| SHOP_FOUNDATION.md | актуален (как foundation) | 15% | Только схема, без логики |
| GIFT_FOUNDATION.md | актуален (как foundation) | 15% | Только схема, без хендлеров |
| ADMIN_PLATFORM.md | актуален | ~85% | RBAC + audit реализованы в боте и UI |
| COMBOT_MIGRATION.md / COMBOT_IMPORT_PLAN.md | актуален | ~80% | Импорт-скрипт есть; разовый процесс |
| MINI_APP_PLAN.md | только-план | 0% | Mini App кода нет |
| ADMIN_UI_PLAN.md (v0-voznya) | частично | ~85% | Большая часть UI реализована |
| TECHNICAL_AUDIT_REPORT.md | устарел | — | Прошлый аудит, до foundation-серии |
| FINAL_IMPLEMENTATION_SPEC.md | устарел | — | Ранний спек |
| IMPLEMENTATION_COMPLETE.md | устарел | — | Маркетинговый «готово» отчёт |
| TARGETED_IMPROVEMENTS.md / UX_*_PLAN.md / UX_*_COMPLETE.md | устарел | — | Планы/отчёты по UX, перекрыты кодом |
| DUEL_BUG_FIX_REPORT.md / OPEN_DUEL_FIX.md | устарел | — | Разовые отчёты о фиксах дуэлей |
| PROFILE_COMMAND_DIAGNOSIS / _FIX / _ERROR_ANALYSIS / _FINAL_REPORT / _FIX_APPLIED / _FIX_SUMMARY | устарел | — | 6 одноразовых отчётов об одном баге /профиль |
| PROOF_OF_FIX.md / HELP_COMMAND_DIAGNOSIS.md | устарел | — | Разовые отладочные заметки |
| DEBUG_INSTRUCTIONS.md / VPS_*_*.md | справочное | — | Операционные шпаргалки, не код |
| DELETION_ARCHITECTURE.md | актуален | ~90% | Описывает `app/services/deletion.py` |
| APPROVED_CHANGES.md | справочное | — | Журнал согласований |

Рекомендация (НЕ выполнять без разрешения): ~14 устаревших одноразовых
`*_FIX/_DIAGNOSIS/_REPORT.md` в корне — кандидаты на перенос в `docs/archive/`.

---

## ЭТАП 3. Миграции

Цепочка линейная, без ветвлений, дубликатов и разрывов. Голова — `0014_mmr_foundation`.

```
0001_initial → 0002_achievements → 0003_loss_counters → 0004_message_stats →
0005_open_duels → 0006_account_links → 0007_account_links_unique_user →
0008_admin_platform → 0009_inventory_foundation → 0010_shop_foundation →
0011_gift_foundation → 0012_combot_import_foundation → 0013_reputation_foundation →
0014_mmr_foundation (HEAD)
```

Карта «таблица → миграция»:
- 0001: users, transactions, marriages, daily_nominations и базовые игровые поля
- 0002: user_achievements
- 0003: счётчики поражений (alter users)
- 0004: message_daily + users.messages_count
- 0005: open duels (поля/таблица дуэлей)
- 0006/0007: account_links (+ UNIQUE по user_id)
- 0008: admin_roles, audit_log
- 0009: inventory, inventory_items, inventory_history
- 0010: shop_categories, shop_offers, purchase_history
- 0011: gift_transactions
- 0012: combot_user_stats, combot_daily_stats, combot_activity_heatmap, combot_import_run
- 0013: reputation_entries
- 0014: mmr_entries

Конфликтов нет. «Используемые vs foundation» таблицы — см. Этап 4.
Шум в репозитории: файл `tatusgit status` в корне (опечатка `> tatus` от
`git status`) — не миграция, безвреден, кандидат на удаление вручную.

---

## ЭТАП 4. Незавершённые foundation

Системы, у которых есть модель + миграция (+ док), но НЕТ живого кода:

### inventory / inventory_items / inventory_history (миграция 0009)
- Готово: модели `app/models/inventory*.py`, схема с частичным уникальным
  индексом «один экипированный предмет на слот».
- Отсутствует: репозиторий, сервис выдачи/экипировки, хендлеры бота, запись
  истории. Сайт читает только агрегаты (`COUNT/SUM`) в `lib/queries.ts`.
- Блокирует запуск: нет API выдачи предметов и нет UI экипировки.

### shop / shop_categories / shop_offers / purchase_history (миграция 0010)
- Готово: схема таблиц.
- Отсутствует: вся логика покупки, списания ешек, витрина, корзина, API.
- Блокирует: нет связи offer → выдача inventory → транзакция.

### gifts / gift_transactions (миграция 0011)
- Готово: схема.
- Отсутствует: хендлеры дарения, проверка `transferable`, перевод предмета.
- Блокирует: зависит от рабочего inventory.

### cosmetics / titles / badges / frames
- Готово: типы в каталоге `inventory_items` (`title/badge/frame/avatar`),
  placeholder `cosmetics` в `lib/queries.ts` (всегда пустой).
- Отсутствует: выдача, экипировка, отрисовка на сайте/в боте.
- Блокирует: зависит от inventory.

Combot import — пограничный случай: скрипт `scripts/import_combot_history.py`
рабочий, но это разовый ручной процесс, не живая интеграция.

---

## ЭТАП 5. Мёртвый / неактивный код (НЕ удалять — только список)

- `app/features/quick/handlers.py` — роутер намеренно пустой, в диспетчере НЕ
  регистрируется (докстринг это подтверждает). Disabled.
- `app/features/profile/handlers.py.new` — артефакт-черновик, не импортируется.
- Модели без живых потребителей (foundation): `inventory`, `inventory_item`,
  `inventory_history`, `shop_category`, `shop_offer`, `purchase_history`,
  `gift_transaction`. Зарегистрированы в `app/models/__init__.py` (нужны для
  Alembic autogenerate), но ни один репозиторий/сервис/хендлер их не читает и не
  пишет.
- Файл `tatusgit status` в корне — мусор.

Активно используемые модели/репозитории/сервисы (reputation, mmr, account_links,
combot_stats, admin/audit, экономические) — НЕ мёртвые, имеют живые вызовы.

---

## ЭТАП 6. Синхронизация BOT ↔ SITE

| Система | Бот | Сайт | Примечание |
|---|---|---|---|
| profile | да | да | сайт читает users + агрегаты; бот рендерит карточку |
| MMR | да (начисление + команды) | да (отображение) | сайт читает `mmr_entries`, блок скрыт без таблицы |
| reputation | да (reply-команды) | да (отображение) | сайт читает `reputation_entries`, блок скрыт без таблицы |
| inventory | нет (foundation) | только статистика (read-only агрегаты) | запись отсутствует везде |
| shop | нет | нет | foundation |
| gifts | нет | нет | foundation |
| исторические сообщения (Combot) | да (импорт-скрипт) | да (единый счётчик current+history) | join по user_id, миграция 0012 |
| auth / OIDC | да (выдача токена линковки) | да (логин, сессии) | связка через account_links |
| admin | да (RBAC в боте) | да (UI + audit) | права зеркалятся `permissions.py` ↔ `admin-permissions.ts` |
| economy / transactions | да | да (read-only + admin write) | сайт пишет только через admin-роуты |

Важно: сайт по `users` — read-only (бот единственный владелец таблицы). Записи на
сайте есть только в admin-роутах (`app/api/admin/economy`, `/inventory`,
`/bootstrap-owner`) и в OIDC-флоу.

Риск рассинхрона: ранги MMR продублированы в двух местах —
`app/settings/mmr.py` (`RANKS`) и `v0-voznya/lib/queries.ts` (`MMR_RANKS`).
Менять нужно синхронно (помечено комментариями в обоих файлах).

---

## ЭТАП 7. Готовность Mini App

Реальная готовность: **0% по коду Mini App, но высокая переиспользуемость данных.**

- Кода Telegram WebApp нет: поиск `initData`/`telegram-web-app`/`WebApp` в
  `v0-voznya` пуст. Есть только `MINI_APP_PLAN.md` (план).
- Что уже можно переиспользовать прямо сейчас:
  - read-only слой `lib/queries.ts` (профиль, MMR, репутация, инвентарь-статы,
    топы, экономика) — готов как источник данных.
  - публичные API-роуты `app/api/*` (achievements, daily, economy, families,
    messages, stats, top-rich, profile/[id], me/summary).
  - компонент `components/profile/player-card.tsx` (адаптивный, mobile-first).
  - сессии и логин (`lib/auth/`) — но для Mini App нужен отдельный путь
    аутентификации через `initData`, которого нет.
- Что отсутствует для запуска: проверка подписи `initData`, точка входа
  `/webapp` (или аналог), регистрация WebApp в BotFather, авторизация без Login
  Widget.
- Что можно запустить уже сейчас: «облегчённый» Mini App = открыть существующий
  `/profile/me` как WebApp-URL. Это даст просмотр профиля внутри Telegram без
  нового кода, но без нативной авторизации `initData` (сессия через cookie/логин).

---

## ЭТАП 8. Приоритеты — ТОП-10 следующих задач

Шкалы 1-10 (сложность / ценность / риск).

| # | Задача | Сложность | Ценность | Риск |
|---|---|---|---|---|
| 1 | Прогнать `alembic upgrade head` на проде (0013, 0014) и задеплоить сайт | 2 | 9 | 3 |
| 2 | Mini App «v0»: открыть `/profile/me` как WebApp + проверка `initData` | 5 | 9 | 4 |
| 3 | Inventory engine: репозиторий + сервис выдачи/экипировки + хендлеры | 7 | 8 | 5 |
| 4 | Shop поверх inventory: витрина, покупка, списание ешек, purchase_history | 8 | 8 | 6 |
| 5 | Архивировать ~14 устаревших `*_FIX/_DIAGNOSIS.md`, обновить AGENTS.md | 1 | 5 | 1 |
| 6 | Удалить мусор `tatusgit status` и `handlers.py.new`, выпилить `quick/` | 2 | 4 | 2 |
| 7 | Вынести ранги MMR в один источник (генерация TS из Python или общий JSON) | 4 | 6 | 3 |
| 8 | Gifts поверх inventory (после shop): дарение `transferable`-предметов | 6 | 6 | 5 |
| 9 | Тесты на foundation-критичное: account_links uniqueness, MMR/reputation агрегаты | 5 | 7 | 2 |
| 10 | Косметика на профиле: экипировка title/frame/badge + отрисовка | 6 | 7 | 4 |

---

## ЭТАП 9. Tech debt (НЕ чинить — только список)

- TODO/FIXME/HACK/XXX в `app/`: **0 хитов** (чисто).
- `except Exception` (~9 мест) — все осознанные и логируемые: `app/main.py`
  (set menu commands), `app/middlewares/db.py` (rollback), `user_tracking.py`
  (ghost-achievement), `app/services/deletion.py` (best-effort delete),
  `features/{marriage,duel,treasure}/handlers.py` (игнор ошибок Telegram-edit).
  Не скрытые баги, но широкие catch — кандидаты на сужение типов.
- `# type: ignore[call-arg]` в `app/config.py:63` — ожидаемо для pydantic.
- Дублирование рангов MMR (Python ↔ TS) — главный смысловой долг (Этап 6).
- Локализация захардкожена в `app/settings/texts.py` (RU-only) — нет i18n-слоя.
- Combot import — разовый скрипт, нет идемпотентной/повторяемой интеграции.
- Артефакты: `handlers.py.new`, `tatusgit status`, disabled `quick/`.
- Россыпь устаревших MD в корне зашумляет навигацию.

---

## ЭТАП 10. Финальные выводы

### 1. Что реально работает сейчас
Полный игровой цикл бота: farm, duel, treasure, casino, marriage/para, pidor,
achievements, balance/economy/transactions, ratings, profile, help. Социальные
системы reputation и MMR (начисление + команды). Auth/OIDC-линковка. Admin
platform (бот RBAC + сайт UI + audit). Сайт: публичные страницы, профиль с MMR/
репутацией/инвентарь-статами/местами в топах, единый счётчик сообщений (current +
Combot history).

### 2. Что существует только на бумаге / foundation
Mini App (только план). Shop, gifts, полноценный inventory (выдача/экипировка),
косметика (titles/badges/frames как предметы) — только модели + миграции + доки.

### 3. Что готово к продакшену
Бот целиком (после применения миграций). Сайт-профиль и admin UI. MMR и
reputation — код готов.

### 4. Что требует миграций
Прод-БД должна быть на `0014_mmr_foundation`. Если ещё нет — применить 0013
(reputation) и 0014 (MMR), иначе соответствующие блоки сайта скрыты, а команды
бота упадут при записи.

### 5. Что требует деплоя
Сайт `v0-voznya`: профильные изменения уже запушены в `origin/main` (коммит
`aa85f8d`) — должен задеплоиться Vercel'ом. Бот: задеплоить ветку с MMR/reputation
и перезапустить процесс.

### 6. Что требует тестирования
account_links uniqueness и захват аккаунта; атомарный consume oidc_link_requests
и TTL-очистка (`app/services/link_maintenance.py`); агрегаты MMR/reputation под
нагрузкой; единый счётчик сообщений после Combot-импорта; RBAC-гейтинг admin.
Автотестов в репозитории не обнаружено — это отдельный пробел.

### 7. Пять рекомендуемых следующих задач
1) Применить миграции на проде + проверить деплой сайта (#1).
2) Mini App v0 на базе существующего профиля и `initData` (#2).
3) Inventory engine — разблокирует shop/gifts/косметику (#3).
4) Гигиена репозитория: архив устаревших MD, удаление артефактов, обновление
   AGENTS.md (#5, #6).
5) Базовые тесты на auth/линковку и агрегаты рейтингов (#9).

### 8. Какие системы лучше НЕ трогать
- Экономика/transactions и `app/core/economy_events.py` — ядро баланса, любой
  сдвиг ломает игру. Менять только через `docs/ECONOMY.md`.
- account_links / OIDC / `link_maintenance.py` — безопасность привязки; трогать
  только с тестами.
- Цепочка миграций 0001-0014 — линейна и чиста; не переписывать прошлые ревизии,
  только добавлять новые.
- Право собственности сайта над `users`: сайт остаётся read-only по этой таблице
  (кроме явных admin-роутов). Не вводить записи в обход бота.

---

*Отчёт основан на чтении кода; автотестов в репозитории нет, поэтому пункты
«требует тестирования» не проверены вживую, а выявлены как пробелы.*
