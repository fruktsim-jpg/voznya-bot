# CASES V1 — DEPLOY & VALIDATION REPORT

Назначение: пошаговый прогон выката Cases V1 — применить миграцию 0016,
проверить, прогнать ручные сценарии, проверить таблицы SQL-запросами и знать
заранее все точки отказа. Используется как runbook при деплое.

Связанные: `CASES_V1_READINESS.md` (аудит готовности), `CASES_V1_PLAN.md`,
`CASES_FIRST_BATCH_PROPOSAL.md`.

Окружение: Python/Alembic/pytest доступны только в Docker (контейнер `bot`).
Сайт (`v0-voznya`) деградирует до пустых блоков, пока 0016 не применена.

---

## 1. Команды применения миграции 0016

### 1.1 Бэкап ПЕРЕД миграцией (обязательно на prod)
```bash
# дамп БД (имя сервиса БД — db, см. docker-compose.yml)
docker compose exec db pg_dump -U $POSTGRES_USER -d $POSTGRES_DB \
  -Fc -f /tmp/voznya_pre_0016.dump
docker compose cp db:/tmp/voznya_pre_0016.dump ./backups/voznya_pre_0016.dump
# (в проекте есть scripts/backup.sh — можно использовать его)
```

### 1.2 Проверить текущее состояние цепочки
```bash
docker compose exec bot alembic current          # ожидаем 0015_user_mmr_projection
docker compose exec bot alembic heads             # один head, без расхождений
docker compose exec bot alembic history | head -30
```

### 1.3 Применить 0016
```bash
docker compose exec bot alembic upgrade head      # → 0016_cases_foundation
docker compose exec bot alembic current           # подтвердить 0016_cases_foundation
```

### 1.4 Проверка обратимости (ТОЛЬКО на dev/stage, НЕ на prod с данными)
```bash
docker compose exec bot alembic downgrade -1       # откат к 0015
docker compose exec bot alembic current            # 0015_user_mmr_projection
docker compose exec bot alembic upgrade head        # снова 0016
```

### 1.5 Импорт приложения и тесты (dev/stage)
```bash
docker compose exec bot python -c "import app.main; print('import OK')"
docker compose exec bot pytest tests/test_cases_pick_reward.py -q
docker compose logs -f bot          # убедиться, что бот поднялся без трейсбеков
```

---

## 2. Проверки сразу после миграции

| # | Проверка | Команда / ожидание |
|---|----------|--------------------|
| 1 | Ревизия применена | `alembic current` → `0016_cases_foundation` |
| 2 | Один head | `alembic heads` → одна строка |
| 3 | 4 таблицы созданы | SQL §4.1 → `inventory_instances`, `case_definitions`, `case_rewards`, `case_openings` присутствуют |
| 4 | Колонки каталога добавлены | SQL §4.2 → `stackable`, `collection_code`, `series_total` в `inventory_items` |
| 5 | `stackable` без server_default | SQL §4.2 → `column_default` IS NULL (дефолт снят, как с transferable) |
| 6 | CHECK'и на месте | SQL §4.3 → 5 constraint'ов на `case_rewards`, 2 на `case_definitions` |
| 7 | Частичный уникальный индекс gift | SQL §4.4 → `uq_inventory_instance_tg_gift` существует |
| 8 | Приложение импортируется | `python -c "import app.main"` без ImportError |
| 9 | Бот живой | `alembic current` из контейнера + логи без трейсбека |
| 10 | Существующие предметы стали stackable=true | SQL §4.2b → нет строк с `stackable IS NULL` |

---

## 3. Ручные сценарии (E2E)

> Предусловие: в `inventory_items` есть предмет-кейс (`type='case'`, напр.
> `case_rookie`) и предмет-награда (напр. титул). Заведение каталога — отдельно
> (SQL/сид), не часть Cases V1.

### A. Настройка кейса (админка)
1. Войти в `/admin/cases` под ролью **admin+** → форма видна.
2. Создать кейс на `case_rookie` (бесплатно, `consumes_key=true`).
3. Добавить 2–3 дропа (currency + item). Проверить, что сумма шансов = 100%.
4. Под ролью **support** → форм нет, только просмотр (RBAC).
5. Каждое действие → строка в `/admin/audit` (`cases.create`/`cases.reward_add`).

### B. Открытие в боте — счастливый путь
6. Выдать себе 1 `case_rookie` (admin item grant через `/api/admin/inventory`).
7. `/кейсы` — кейс в списке, «у тебя: 1».
8. `/кейс case_rookie` — карточка, дроп-лист с шансами, кнопка «Открыть».
9. `/открыть case_rookie` (или кнопка) — награда выдана, кейс списан, ответ
   корректен. Проверить SQL §4.5 (строка `case_openings` с `roll`/`weight_
   snapshot`), §4.6 (inventory изменился), §4.7 (если currency — transaction).

### C. Негативные сценарии (КРИТИЧНО — проверяют фикс)
10. **Нет ключа:** `/открыть case_rookie` без кейса → «нет кейса». SQL §4.5 — НЕ
    появилось новой строки открытия; инвентарь не тронут.
11. **Платный кейс, не хватает ешек:** создать кейс с
    `open_cost_kind=currency`, `consumes_key=true`; выдать ключ, обнулить баланс;
    `/открыть` → «не хватает». **КРИТИЧНО:** SQL §4.6 — ключ НА МЕСТЕ (не списан),
    баланс не изменился, открытия нет. (Регрессия дыры из `READINESS §6`.)
12. **Чужая кнопка:** второй пользователь жмёт «Открыть» → «не твой кейс»,
    ничего не происходит.
13. **Двойной клик:** быстро нажать «Открыть» дважды при `quantity=1` → ровно
    одно открытие (SQL §4.5 — одна новая строка), второй → «нет кейса».
14. **Лимитка:** дроп с `max_global_supply=1`; после первого выпадения SQL §4.8
    → `granted_count=1`; далее эта награда исключается из розыгрыша.
15. **Пустой/исчерпанный дроп-лист:** кейс без доступных наград → открытие
    откатывается (исключение), ключ/ешки НЕ списаны (rollback).

### D. Сайт
16. `/cases` (без логина) — витрина активна, шансы совпадают с админкой.
17. `/cases` до миграции (или при пустых таблицах) — дружелюбный empty-state,
    без 500.

---

## 4. SQL-запросы для проверки таблиц

### 4.1 Таблицы созданы
```sql
SELECT table_name FROM information_schema.tables
 WHERE table_schema = 'public'
   AND table_name IN ('inventory_instances','case_definitions',
                      'case_rewards','case_openings')
 ORDER BY table_name;   -- ожидаем 4 строки
```

### 4.2 Колонки каталога
```sql
SELECT column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_name = 'inventory_items'
   AND column_name IN ('stackable','collection_code','series_total')
 ORDER BY column_name;
-- stackable: boolean, NOT NULL, default NULL (server_default снят)
```
```sql
-- 4.2b: существующие предметы получили stackable=true (не NULL)
SELECT COUNT(*) AS null_stackable FROM inventory_items WHERE stackable IS NULL;
-- ожидаем 0
```

### 4.3 CHECK-constraints
```sql
SELECT conname FROM pg_constraint
 WHERE conrelid = 'case_rewards'::regclass AND contype = 'c'
 ORDER BY conname;
-- ck_case_rewards_weight_pos, ck_case_rewards_qty,
-- ck_case_rewards_granted_nonneg, ck_case_rewards_supply,
-- ck_case_rewards_kind_payload
SELECT conname FROM pg_constraint
 WHERE conrelid = 'case_definitions'::regclass AND contype = 'c'
 ORDER BY conname;
-- ck_case_def_cost_nonneg, ck_case_def_cost_kind
```

### 4.4 Индексы (включая частичные)
```sql
SELECT indexname FROM pg_indexes
 WHERE tablename IN ('inventory_instances','case_definitions',
                    'case_rewards','case_openings')
 ORDER BY indexname;
-- среди них: uq_inventory_instance_tg_gift, ix_case_definitions_active,
-- ix_case_rewards_case, ix_case_openings_user, ix_case_openings_case
```

### 4.5 Леджер открытий (после теста)
```sql
SELECT id, user_id, case_item_code, reward_kind, reward_item_code,
       amount, qty, roll, weight_snapshot, created_at
  FROM case_openings
 ORDER BY created_at DESC
 LIMIT 10;
-- проверить: roll в [0, weight_snapshot->>'total'), снимок весов присутствует
```

### 4.6 Владение кейсом/наградой у игрока
```sql
SELECT user_id, item_code, quantity, equipped, source
  FROM inventory
 WHERE user_id = :uid
 ORDER BY item_code;
-- после успешного открытия: -1 кейс, +1 предмет-награда (если item)
-- после "не хватает ешек": кейс на месте (quantity не уменьшился)
```

### 4.7 Движение ешек (для currency-наград и платных открытий)
```sql
SELECT id, amount, reason, meta, created_at
  FROM transactions
 WHERE user_id = :uid
 ORDER BY created_at DESC
 LIMIT 10;
-- открытие за ешки: reason='purchase' (EVENT_PURCHASE), amount<0
-- денежная награда: reason='reward'  (EVENT_REWARD),  amount>0
```

### 4.8 Лимитки (джекпоты)
```sql
SELECT id, case_item_code, reward_kind, max_global_supply, granted_count,
       is_jackpot
  FROM case_rewards
 WHERE max_global_supply IS NOT NULL
 ORDER BY case_item_code, id;
-- granted_count никогда не превышает max_global_supply (CHECK гарантирует)
```

### 4.9 Сверка дроп-листа и шансов (что увидит игрок)
```sql
SELECT r.case_item_code, r.reward_kind,
       COALESCE(i.name, r.reward_item_code, r.amount::text) AS reward,
       r.weight,
       ROUND(100.0 * r.weight
             / SUM(r.weight) OVER (PARTITION BY r.case_item_code), 2) AS pct
  FROM case_rewards r
  LEFT JOIN inventory_items i ON i.code = r.reward_item_code
 WHERE r.reward_kind IN ('item','currency')   -- V1-скоуп (как фильтрует open_case)
 ORDER BY r.case_item_code, r.weight DESC;
```

### 4.10 Кейсы-определения и связь с каталогом
```sql
SELECT d.item_code, d.name, d.open_cost_kind, d.open_cost_amount,
       d.consumes_key, d.is_active,
       i.type AS catalog_type
  FROM case_definitions d
  LEFT JOIN inventory_items i ON i.code = d.item_code
 ORDER BY d.is_active DESC, d.name;
-- catalog_type должен быть 'case' для каждого кейса; NULL = висячая ссылка
```

### 4.11 Висячие награды (item-дроп без предмета в каталоге)
```sql
SELECT r.id, r.case_item_code, r.reward_item_code
  FROM case_rewards r
 WHERE r.reward_kind = 'item'
   AND NOT EXISTS (SELECT 1 FROM inventory_items i WHERE i.code = r.reward_item_code);
-- ожидаем 0 строк (иначе открытие упадёт UnknownItem при выпадении)
```

---

## 5. Возможные точки отказа

| # | Точка отказа | Симптом | Причина / проверка | Митигация |
|---|--------------|---------|--------------------|-----------|
| 1 | Миграция не с того head | `alembic upgrade` ругается на ревизию | текущая ревизия ≠ 0015 | `alembic current`; догнать цепочку |
| 2 | 0016 частично применилась | таблицы есть, индексов нет | прерывание посреди upgrade | откатить из бэкапа §1.1; повторить |
| 3 | `stackable` остался NULL у старых строк | SQL §4.2b > 0 | server_default не отработал | проверить миграцию; `UPDATE ... SET stackable=true` |
| 4 | Кейс без `type='case'` в каталоге | `/admin/cases` POST → 409 | item не того типа | завести/исправить предмет каталога |
| 5 | item-награда без предмета в каталоге | открытие падает `UnknownItem`, rollback | SQL §4.11 > 0 | удалить/исправить строку дропа |
| 6 | Дроп-лист пуст или все исчерпаны | `/открыть` → ошибка, rollback | нет доступных наград | добавить дроп / снять лимиты |
| 7 | tg_gift/stars-строка в дропе V1 | награда молча не выпадает | `open_case` фильтрует REWARD_KINDS_V1 | админ-API уже блокирует ввод; для старых данных — SQL-чистка |
| 8 | **Потеря ключа при платном открытии** | ключ исчез, награды нет | **ИСПРАВЛЕНО** (pre-flight); регресс-тест §3.11 | если всплывёт — это регрессия service.py |
| 9 | Гонка двойного клика | два открытия одним ключом | блокировки FOR UPDATE в open_case | регресс-тест §3.13 |
| 10 | Лимитка ушла в минус остатка | `granted_count > max_global_supply` | CHECK `ck_case_rewards_supply` + FOR UPDATE | БД отвергнет; проверить §4.8 |
| 11 | Сайт 500 на `/cases` или `/admin/cases` | белый экран/ошибка | таблиц нет (до миграции) | try/catch уже отдаёт пустой список — проверить, что catch на месте |
| 12 | Рассинхрон прав cases.* Python↔TS | действие доступно в боте, но не на сайте (или наоборот) | правки только в одном месте | сверить `permissions.py` ↔ `admin-permissions.ts` |
| 13 | `BOT_COMMANDS` без /cases | команда работает, но нет в меню | сознательно не добавлено | добавить при согласовании меню |
| 14 | Прод-даунгрейд с данными | потеря `case_*` таблиц | `downgrade` дропает таблицы | НЕ делать downgrade на prod; только бэкап-restore |

---

## 6. Критерии «выкат прошёл»
- [ ] §2 — все 10 проверок зелёные.
- [ ] §3.A–D — ручные сценарии пройдены, особенно §3.11 (потеря ключа) и §3.13
  (двойной клик).
- [ ] §4.11 и §4.10 — нет висячих ссылок.
- [ ] `pytest tests/test_cases_pick_reward.py` — зелёный.
- [ ] Бэкап §1.1 сохранён до миграции.
- [ ] Сайт `/cases` и `/admin/cases` открываются без 500.

После выката — наполнение реальными кейсами по `CASES_FIRST_BATCH_PROPOSAL.md`
(с отдельным баланс-документом).
