# COMBOT IMPORT PLAN — фундамент исторического импорта

Foundation для сохранения всей исторической статистики Возни из Combot в
собственную БД. Это **только фундамент**: схема (модели + одна Alembic-миграция)
и чистый слой правил достижений. Импорт не запускался, миграция не применялась,
production не трогался.

Дата: 2026-06-06. Источник данных и подтверждённые факты — см. `COMBOT_MIGRATION.md`.

## Жёсткие ограничения (соблюдены)
- Зависимость от Combot НЕ удаляется — это «точка сохранения» до завершения импорта.
- НЕ трогаем: `users`, баланс/`transactions`, `inventory*`, `shop_*`, `gift_transactions`.
  Все новые таблицы изолированы, связи логические (`import_run_id`), без FK.
- Нет UI. Нет запуска импорта. Нет автоприменения миграции. Только FOUNDATION.

---

## 1. Спроектированные таблицы

Четыре изолированные таблицы с префиксом `combot_`. ORM-модели в `app/models/`,
схема — в миграции `migrations/versions/0012_combot_import_foundation.py`
(revision `0012_combot_import_foundation`, поверх `0011_gift_foundation`).

### `combot_import_runs` — журнал прогонов
Идемпотентность и аудит: видно, делался ли импорт, что тянули, сколько записали.

| Колонка | Тип | Назначение |
|---------|-----|-----------|
| `id` | BigInteger PK | id прогона |
| `status` | String(16) | `running` / `success` / `failed` (CHECK) |
| `range_from_ms`, `range_to_ms` | BigInteger | диапазон выгрузки (Unix ms) |
| `users_imported` | Integer | сколько строк в `combot_user_stats` |
| `days_imported` | Integer | сколько строк в `combot_daily_stats` |
| `heatmap_cells_imported` | Integer | сколько ячеек heatmap |
| `started_by` | BigInteger NULL | user_id админа (NULL для CLI) |
| `error` | String(512) NULL | текст ошибки при `failed` |
| `meta` | JSONB NULL | произвольные детали прогона |
| `started_at`, `finished_at` | timestamptz | тайминги |

### `combot_user_stats` — пер-юзерный снимок (задача 2)
Источник: `channel_users`. PK = `user_id` → повторный импорт делает upsert.

| Колонка | Источник (Combot) | Тип |
|---------|-------------------|-----|
| `user_id` PK | `user_id` | BigInteger |
| `username` | `u[0].username` | String(64) NULL |
| `title` | `u[0].title` | String(256) NULL |
| `joined_at` | `joined` (ms→tz) | timestamptz NULL |
| `days_since_joined` | `dsj` | Integer NULL |
| `messages` | `messages` | Integer |
| `xp` | `xp` | Integer |
| `rep` | `rep` | Integer |
| `last_message_at` | `last_message` (ms→tz) | timestamptz NULL |
| `import_run_id` | — | BigInteger NULL (логическая связь) |
| `raw` | вся запись юзера | JSONB NULL |
| `imported_at` | now() | timestamptz |

Индексы: `ix_combot_user_messages(messages)` — топы; `ix_combot_user_joined(joined_at)` — старожилы.

Покрывает требуемые поля задачи 2: `user_id, username, joined, messages, xp, rep, last_message`.

### `combot_daily_stats` — дневная история (задача 3)
Источник: тайм-серии `channel_analytics`. PK = `day` (UTC).

| Колонка | Источник | Тип |
|---------|----------|-----|
| `day` PK | ts пары `[ts_ms, n]` → дата | Date |
| `messages` | `analytics.messages` | Integer |
| `active_users` | `analytics.active_users` | Integer |
| `joins` | `analytics.joined` | Integer |
| `leaves` | `analytics.left` | Integer |
| `import_run_id` | — | BigInteger NULL |
| `imported_at` | now() | timestamptz |

Покрывает задачу 3: `messages, active_users, joins, leaves` по дням.

### `combot_activity_heatmap` — тепловая карта (задача 4)
Источник: `analytics.hours` (тройки `[hour, weekday, count]`). PK = (`hour`,`weekday`).

| Колонка | Источник | Тип |
|---------|----------|-----|
| `hour` PK | `hours[][0]` | SmallInteger (CHECK 0–23) |
| `weekday` PK | `hours[][1]` | SmallInteger (CHECK 0–6) |
| `messages` | `hours[][2]` | Integer |
| `import_run_id` | — | BigInteger NULL |
| `imported_at` | now() | timestamptz |

### Связи
```
combot_import_runs (1) ──logical(import_run_id)──> combot_user_stats     (N)
                        ──logical(import_run_id)──> combot_daily_stats    (N)
                        ──logical(import_run_id)──> combot_activity_heatmap(N)
```
Без FK сознательно: история самодостаточна и переживает любые чистки прочих таблиц.

---

## 2–4. Что и куда сохраняем (маппинг)

- **Пер-юзер** (задача 2) → `combot_user_stats`. Преобразования: `joined`/
  `last_message` из Unix **ms** → `timestamptz`; `u[0]` распаковываем в
  `username`/`title`; полную запись кладём в `raw`.
- **Дневная история** (задача 3) → `combot_daily_stats`. Четыре тайм-серии
  (`messages`, `active_users`, `joined`, `left`) сводим по дню в одну строку.
- **Heatmap** (задача 4) → `combot_activity_heatmap`, по ячейке на тройку.

Аватары (base64 из `last_*_users`) сознательно НЕ импортируем в foundation —
тяжёлые и не нужны для статистики; при желании добавятся отдельной таблицей позже.

---

## 5. Одноразовый импорт (дизайн, без реализации)

Импорт-скрипт ещё не написан — здесь только согласованный план. Предлагаемое
место: `scripts/import_combot_history.py` (CLI, запуск вручную, НЕ из бота).

Алгоритм:
1. Создать строку `combot_import_runs` со `status='running'`, зафиксировать
   `range_from_ms/to_ms`.
2. `GET channel_users?limit=500&page=0` → один ответ, все ~405 участников.
   Upsert в `combot_user_stats` по `user_id` (`ON CONFLICT DO UPDATE`).
   При `total>limit` — добежать страницы по `page`.
3. `GET channel_analytics?from=<начало>&to=<сейчас>` → разложить тайм-серии в
   `combot_daily_stats` (upsert по `day`) и `analytics.hours` в
   `combot_activity_heatmap` (upsert по `hour,weekday`).
4. Проставить счётчики, `status='success'`, `finished_at=now()`. При исключении —
   `status='failed'` + `error`.

Идемпотентность: все три набора — upsert по натуральным ключам, поэтому повторный
прогон безопасен и просто обновляет снимок. `combot_import_runs` хранит историю
запусков.

Конфигурация: `COMBOT_API_KEY`, `COMBOT_CHAT_ID` через env (ключ — секрет, не в
git). Запуск только вручную и сначала на dev/staging.

---

## 6. Слой достижений (подготовка, без выдачи)

Файл: `app/settings/combot_historical_achievements.py`. Это **чистые правила**:
пороги + функция-классификатор `evaluate_historical_tiers(...)`. НЕ пишет в БД,
НЕ начисляет ешки, НЕ трогает боевой `user_achievements`.

| Код | Бейдж | Условие (стартовые пороги) |
|-----|-------|----------------------------|
| `combot_oldtimer` | ⏳ Старожил | в чате ≥ 365 дней |
| `combot_legend` | 👑 Легенда | ≥ 5000 сообщений |
| `combot_activist` | 🔥 Активист | ≥ 500 сообщений |
| `combot_veteran` | 🎖️ Ветеран | ≥ 180 дней И ≥ 1000 сообщений |

Дни в чате берём из `days_since_joined` (`dsj`), иначе считаем по `joined_at`.
Пороги предварительные (калибровка по факту импорта: top-1 ≈ 14k сообщений,
история ~2 года). Меняются в одном месте.

Будущая выдача (отдельная задача, НЕ здесь): прогнать `combot_user_stats` через
`evaluate_historical_tiers`, смаппить коды на боевые достижения и при желании
выдать через существующий `app/features/achievements/service.py`.

---

## 7–9. Границы (что НЕ сделано намеренно)
- ❌ UI — нет.
- ❌ Запуск импорта / запись в `combot_*` — нет (таблицы пустые до прогона).
- ❌ Автоприменение миграции — нет. Применять вручную:
  `alembic upgrade 0012_combot_import_foundation` (или `head`), сначала на dev.
- ❌ Изменения `users`/баланса/`inventory`/`shop`/`gift` — нет.
- ❌ Удаление зависимости от Combot — нет.

---

## Чек-лист созданных файлов
- `app/models/combot_user_stats.py` — модель `CombotUserStats`
- `app/models/combot_daily_stats.py` — модель `CombotDailyStats`
- `app/models/combot_activity_heatmap.py` — модель `CombotActivityHeatmap`
- `app/models/combot_import_run.py` — модель `CombotImportRun`
- `app/models/__init__.py` — регистрация 4 моделей
- `migrations/versions/0012_combot_import_foundation.py` — схема (ручное применение)
- `app/settings/combot_historical_achievements.py` — правила исторических бейджей

## Следующие шаги (вне этой задачи)
1. Написать `scripts/import_combot_history.py` по алгоритму из раздела 5.
2. Применить миграцию на dev, прогнать импорт на dev, сверить итоги с
   `COMBOT_MIGRATION.md` (405 юзеров, messages_total 94 960).
3. Откалибровать пороги бейджей по реальному распределению.
4. Только после успешного импорта в prod — рассматривать отказ от Combot.
