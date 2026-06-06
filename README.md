# Возня

Экосистема Telegram-сообщества «Возня»: игровой бот, публичный сайт со
статистикой и профилями, и админ-панель. Две кодовые базы:

- **`voznya-bot`** (этот репозиторий) — Telegram-бот на Python (aiogram) +
  PostgreSQL + Alembic. Единственный владелец игровых данных.
- **`v0-voznya`** — сайт и админ-панель на Next.js. По таблице `users` —
  **только чтение**; пишет лишь через явные админ-роуты и OIDC-флоу.

---

## Архитектура

```
        ┌─────────────┐
        │   Telegram  │
        └──────┬──────┘
               │ aiogram (polling/webhook)
               ▼
        ┌─────────────┐        пишет игровые данные
        │     Bot     │───────────────────────────────┐
        │ (voznya-bot)│                                │
        └─────────────┘                                ▼
                                              ┌──────────────────┐
                                              │    PostgreSQL    │
                                              │  (источник правды)│
                                              └────────┬─────────┘
                                       read-only (+admin write)│
                                                       ▼
                                              ┌──────────────────┐
                                              │   Website (Next) │
                                              │   v0-voznya      │
                                              └────────┬─────────┘
                                                       ▼
                                              ┌──────────────────┐
                                              │   Admin Panel    │
                                              │  (/admin, RBAC)  │
                                              └──────────────────┘
```

Бот — единственный, кто пишет в `users` и игровые таблицы. Сайт читает их для
публичной статистики и профилей; запись возможна только через
аутентифицированные админ-роуты (`/api/admin/*`) и OIDC-привязку.

---

## Что есть (реализовано и работает)

| Система | Где |
|---|---|
| **Telegram Bot** — полный игровой цикл | `app/` (17 зарегистрированных роутеров) |
| **Website** — публичная статистика, профили, лидерборды | `v0-voznya/app`, `v0-voznya/lib/queries.ts` |
| **Admin Panel** — RBAC, управление игроками, экономикой, audit | `app/features/admin/`, `v0-voznya/app/admin/*` |
| **OIDC** — привязка Telegram-аккаунта к сессии сайта | `app/features/linking/`, `v0-voznya/app/api/auth/telegram/oidc/*` |
| **Reputation** — социальный рейтинг reply-фразами | `app/features/reputation/` |
| **MMR** — единый игровой рейтинг + ранги | `app/features/mmr/` |
| **Achievements** — достижения | `app/features/achievements/` |
| **Economy** — ешки, транзакции | `app/core/economy_events.py`, `transactions` |
| **Families / Marriages** — семьи и браки | `app/features/marriage/`, `app/features/para/` |
| **Farm** — ферма со стриками | `app/features/farm/` |
| **Treasure** — клады | `app/features/treasure/` |
| **Casino, Duel, Pidor дня** — игровые механики | `app/features/{casino,duel,pidor}/` |
| **Combot Import** — импорт исторических сообщений | `scripts/import_combot_history.py` |

Подробности по подсистемам — в соответствующих `*_FOUNDATION.md`,
`ADMIN_PLATFORM.md`, `COMBOT_IMPORT_PLAN.md`, `docs/ECONOMY.md`.

---

## Что foundation-only (схема есть, рантайм-кода нет)

Эти системы спроектированы (модели + миграции + доки), но НЕ имеют живого кода:
ни хендлеров бота, ни API, ни UI. Не использовать как рабочие.

| Система | Что есть | Чего нет | Док |
|---|---|---|---|
| **Inventory** | 3 модели + миграция 0009 | репозиторий, выдача/экипировка, хендлеры | `INVENTORY_FOUNDATION.md` |
| **Shop** | 3 модели + миграция 0010 | логика покупки, витрина, API | `SHOP_FOUNDATION.md` |
| **Gifts** | модель + миграция 0011 | хендлеры дарения, перевод предметов | `GIFT_FOUNDATION.md` |
| **Cosmetics** (titles/badges/frames) | типы в каталоге, placeholder на сайте | выдача, экипировка, отрисовка | (часть inventory) |
| **Mini App** | план | весь код Telegram WebApp | `MINI_APP_PLAN.md` |

Полная картина состояния — в `PROJECT_STATE_REPORT.md`.

---

## Миграции

Alembic, линейная цепочка `0001` → `0014`. Текущий HEAD:
**`0014_mmr_foundation`**.

```
0001_initial → 0002_achievements → 0003_loss_counters → 0004_message_stats →
0005_open_duels → 0006_account_links → 0007_account_links_unique_user →
0008_admin_platform → 0009_inventory_foundation → 0010_shop_foundation →
0011_gift_foundation → 0012_combot_import_foundation → 0013_reputation_foundation →
0014_mmr_foundation
```

```bash
docker compose exec bot alembic current       # текущая ревизия
docker compose exec bot alembic upgrade head   # применить до HEAD
```

---

## Быстрый запуск

```bash
# 1. Конфигурация
cp .env.example .env          # заполнить BOT_TOKEN, DATABASE_URL и пр.

# 2. Запуск через Docker Compose (бот + PostgreSQL)
docker compose up -d

# 3. Применить миграции до актуальной схемы
docker compose exec bot alembic upgrade head

# 4. Логи
docker compose logs -f bot
```

Локально без Docker: Python 3.12, `pip install -r requirements.txt`, поднять
PostgreSQL, прописать `DATABASE_URL`, затем `alembic upgrade head` и
`python -m app.main`.

Конфигурация — `app/config.py` (pydantic settings), переменные — в
`.env.example`.

---

## Документация

- `PROJECT_STATE_REPORT.md` — честный аудит состояния всего проекта.
- `REPOSITORY_CLEANUP_REPORT.md` — итог уборки репозитория.
- `FOUNDATION_STATUS.md` — состояние auth/моделей/расширений.
- `ADMIN_PLATFORM.md` — RBAC и audit.
- `MMR_FOUNDATION.md`, `REPUTATION_FOUNDATION.md` — реализованные системы.
- `INVENTORY_FOUNDATION.md`, `SHOP_FOUNDATION.md`, `GIFT_FOUNDATION.md` —
  foundation-only.
- `COMBOT_IMPORT_PLAN.md`, `COMBOT_MIGRATION.md` — импорт истории.
- `MINI_APP_PLAN.md` — план Mini App (не реализован).
- `docs/ECONOMY.md` — баланс экономики.
- `docs/archive/` — устаревшие одноразовые отчёты (история, не актуальны).
