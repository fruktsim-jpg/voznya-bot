# Сезон 1 — подготовка деплоя и smoke-test

Документ для безопасного вывода Сезона 1 в прод. Порядок строгий:
**бэкап → схема → сид → smoke-test на staging → вайп → старт сезона**.

Вайп (0034) НЕОБРАТИМ — выполняется только после успешного smoke-test.

---

## 0. Что вошло в релиз

Бот (voznya-bot):
* `app/settings/season.py` — конфиг (дивизионы, daily, миссии, антиабуз, кейс, титулы);
* `app/models/season.py`, `app/models/season_progress.py` — модели;
* `app/repositories/season.py` — слой данных + анти-дуэль-фарм запрос;
* `app/features/season/{service,handlers}.py` — логика + команды
  (`сезон`, `бонус`, `миссии`, `топсезон`, `стартсезон`, `финалсезон`);
* врезки: `mmr/service.py` (зеркало season MMR), `farm/service.py` (миссия farm),
  `duel/service.py` (миссия duel_win + анти-фарм участия);
* миграции `0033` (схема), `0034` (вайп), `0035` (сезонный кейс);
* тесты `tests/test_season_logic.py`.

Сайт (v0-voznya):
* `lib/season.ts` — чтение (топ/дивизионы/профиль) + админ-действия (старт/финал);
* `app/admin/season/` — управление сезоном (старт/финал, обзор);
* `app/season/page.tsx` — публичная страница сезона (топ, дивизионы, мой блок);
* `components/profile/season-badge.tsx` — сезонный блок в профиле;
* nav: пункт «Сезон» в админке и в меню пользователя.

---

## 1. Деплой (безопасная часть, обратимо)

```bash
git pull

# Бэкап ДО любых изменений (обязательно).
pg_dump "$DATABASE_URL" > backup_pre_season1_$(date +%F).sql

# Схема сезона (обратимо — есть downgrade).
alembic upgrade 0033_season_system

# Сезонный кейс + дроп-лист (идемпотентно).
alembic upgrade 0035_seed_season_1

# Прогон логических тестов бота.
pytest -q tests/test_season_logic.py

# Сборка и перезапуск сайта/бота.
docker compose up -d --build
```

После этого шага игра работает как раньше + появилась схема сезона, но сезон
ещё НЕ запущен и вайп НЕ сделан. Можно спокойно гонять smoke-test.

---

## 2. SMOKE-TEST (обязательно перед вайпом)

Выполнять на staging-копии прод-БД (или на проде сразу после шага 1, ДО вайпа).
Каждый шаг — команда и ожидаемый результат. Если любой шаг падает — деплой
останавливается, вайп НЕ выполняется.

### 2.1. Миграции применяются

```bash
alembic upgrade 0033_season_system && \
alembic upgrade 0035_seed_season_1 && \
alembic current
# Ожидаемо: alembic current показывает 0035_seed_season_1 (head), без ошибок.
```

Примечание: 0034 (вайп) в smoke-test на staging применяется отдельно (шаг 2.3),
на проде — только в окне обслуживания (раздел 3).

### 2.2. Бот стартует и сезон создаётся

1. Бот поднят (`docker compose ps` → bot healthy, в логах «Бот @... запущен»).
2. В чате от админа: `/стартсезон Сезон 1`
   * Ожидаемо: «✅ Сезон «Сезон 1» (#N) запущен на 56 дней».
   * Проверка БД: `SELECT id,name,is_active,ends_at FROM seasons WHERE is_active;`
     → одна строка, `ends_at ≈ now + 56d`.

### 2.3. Вайп (только staging) и идентичность сохранена

```bash
alembic upgrade 0034_season_1_wipe
psql "$DATABASE_URL" -c \
  "SELECT count(*) users, sum(balance) bal, sum(mmr) mmr, sum(season_mmr) smmr, sum(messages_count) msgs FROM users;"
# Ожидаемо: users>0, bal=0, mmr=0, smmr=0, msgs>0 (аккаунты и сообщения целы).
```

После вайпа пересоздать сезон: `/стартсезон Сезон 1` (вайп чистит и seasons).

### 2.4. Начисляется season MMR

В чате: `/ферма` (успешная). Затем:
```sql
SELECT mmr, season_mmr FROM users WHERE user_id = <твой id>;
-- Ожидаемо: оба > 0 и выросли на одну и ту же величину (award_mmr зеркалит).
SELECT source, amount FROM season_mmr_entries WHERE player_id=<id> ORDER BY id DESC LIMIT 3;
-- Ожидаемо: строка с source='farm'.
```

### 2.5. Работает daily

```
/бонус   → «🎁 Ежедневная награда: +N ешек, 🔥 Серия: 1 дн.»
/бонус   → «🎁 Сегодня награду уже забрал...» (повтор не начисляет)
```
БД: `SELECT * FROM daily_claims WHERE player_id=<id>;` → ровно одна строка за сегодня.

### 2.6. Работает weekly

`/ферма` несколько раз, затем `/миссии` — прогресс «Ферми 20 раз» растёт.
При достижении 20 — начисляется +60 ешек и +20 MMR один раз (идемпотентно).
БД: `SELECT mission_code,progress,claimed_at FROM weekly_mission_progress WHERE player_id=<id>;`

### 2.7. Открывается сезонный кейс

На сайте `/cases` или в боте открыть `case_season_1` (цена 600).
* Ожидаемо: списано 600 ешек, выдан приз из дроп-листа; баланс обновился без F5.
* БД: `SELECT reason,amount FROM transactions WHERE user_id=<id> ORDER BY id DESC LIMIT 3;`

### 2.8. Выдаётся сезонная лимитка

Открывать `case_season_1` до выпадения лимитки (gift_*). Редкое событие —
для теста можно временно поднять её вес в `case_rewards` на staging, затем
вернуть. Ожидаемо: предмет появляется в инвентаре игрока.

### 2.9. Завершается сезон и выдаётся титул

В админке `/admin/season` → «Завершить сезон» (или в боте `/финалсезон`).
* Ожидаемо: топ-игроки получают ешки по дивизиону; #1 — титул `s1_champion`.
* БД: `SELECT player_id,code FROM season_titles;` → есть строки.
* `SELECT is_active,finalized_at FROM seasons ORDER BY id DESC LIMIT 1;`
  → `is_active=false`, `finalized_at` заполнен.
* На сайте: титул виден на `/season` (мой блок) и в `/profile/<id>` (бейдж).

---

## 3. Прод-вайп и старт (после успешного smoke-test)

```bash
docker compose stop bot
pg_dump "$DATABASE_URL" > backup_final_pre_wipe_$(date +%F).sql  # ещё один свежий бэкап
alembic upgrade 0034_season_1_wipe
docker compose start bot
# Старт сезона:
#   в чате от админа: /стартсезон Сезон 1
```

Объявить старт в чате (дивизионы, награды, 56 дней).

---

## 4. Мониторинг первых 72 часов

* `/admin/economy` — инфляция/перелив (нетто денежной массы);
* `/admin/season` — распределение по дивизионам, рост топа;
* `/admin/cases` — фактический RTP сезонного кейса (должен быть <1).

Если RTP кейса уплывает или daily/weekly слишком mint-ят — править веса в
`case_rewards` / числа в `app/settings/season.py` (перезапуск/сброс кэша).
