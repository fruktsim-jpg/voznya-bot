# Возня

Экосистема Telegram-сообщества «Возня»: игровой бот, публичный сайт со
статистикой и профилями, и админ-панель. Две кодовые базы:

- **`voznya-bot`** (этот репозиторий) — Telegram-бот на Python (aiogram) +
  PostgreSQL + Alembic. Единственный владелец игровых данных.
- **`v0-voznya`** — сайт и админ-панель на Next.js. По таблице `users` —
  **только чтение**; пишет лишь через явные админ-роуты и OIDC-флоу.

Сводная документация по обеим кодовым базам находится в `../docs/`. Точка входа —
`ARCHITECTURE_INDEX.md` (канонический обзор) и `CURRENT_STATE.md` (актуальное
состояние, миграции, тесты, приоритеты). Детали по подсистемам — в
`docs/features/*` и `docs/drun/`. Исторические отчёты — в `docs/archive/`.

> Источник истины — код, а не документация. Этот README описывает фактическое
> состояние на момент правки. Завершённые планы и одноразовые отчёты лежат в
> `docs/archive/` и НЕ отражают актуальное состояние.

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
                                              │ (источник правды) │
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
публичной статистики, профилей и витрин; запись возможна только через
аутентифицированные админ-роуты (`/api/admin/*`) и OIDC-привязку.

---

## Что реализовано и работает (live)

Бот регистрирует игровые роутеры в `app/features/__init__.py`
(`get_feature_routers`). Полный список — там; ключевое:

| Система | Где (бот) | Где (сайт) |
|---|---|---|
| **Economy / transactions** — ешки, баланс, леджер | `app/core/`, `app/features/balance/` | read-only + admin write |
| **Farm** — ферма со стриками | `app/features/farm/` | — |
| **Casino / Duel / Pidor дня / Treasure** — игровые механики | `app/features/{casino,duel,pidor,treasure}/` | витрина казино `app/casino/` |
| **Marriage / Para** — семьи и пары | `app/features/{marriage,para}/` | топ семей на `/live` |
| **Reputation** — социальный рейтинг reply-фразами | `app/features/reputation/` | блок в профиле |
| **MMR** — единый рейтинг + ранги | `app/features/mmr/` | блок в профиле, ранги |
| **Achievements** — достижения | `app/features/achievements/` | каталог на `/live` |
| **Inventory** — стековый инвентарь + история выдачи | `app/features/inventory/`, `app/services/inventory_grant.py` | статы/витрина в профиле |
| **Cases** — кейсы (открытие, дроп-лист, append-only леджер) | `app/features/cases/` | витрина `/cases` + админка |
| **Gifts Shop** — покупка за ешки + доставка Telegram-подарков | `app/features/gifts/` | коллекция `/gifts` + очередь доставки в админке |
| **Stars** — пополнение баланса через Telegram Stars | `app/features/payments/` | — |
| **Profile** — карточка игрока | `app/features/profile/` | `/profile/[id]`, `/profile/me` |
| **Ratings / Social / Welcome / Help** | `app/features/{ratings,social,welcome,help}/` | топы на `/live` |
| **OIDC** — привязка Telegram-аккаунта к сессии сайта | `app/features/linking/` | `/api/auth/telegram/oidc/*` |
| **Admin platform** — RBAC + audit | `app/features/admin/`, `app/core/permissions.py` | `/admin/*` |
| **Combot import** — импорт исторических сообщений (разовый скрипт) | `scripts/import_combot_history.py` | единый счётчик сообщений |

---

## Миграции

Alembic, линейная цепочка `0001` → `0052` (актуальный HEAD —
`0052_drun_events`). Ревизии `0045`–`0052` — AI/Drun-подсистема (`world_events`,
`ai_*`, embeddings, `drun_events`). Применять:

```bash
docker compose exec bot alembic current        # текущая ревизия
docker compose exec bot alembic upgrade head    # применить до HEAD
docker compose exec bot alembic downgrade -1    # откатить одну ревизию
```

Точная цепочка — в `migrations/versions/`. Прошлые ревизии не переписываются,
только добавляются новые поверх HEAD.

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
`python -m app.main`. Конфигурация — `app/config.py` (pydantic settings),
переменные — в `.env.example`.

---

## Документация

Актуальная (поддерживается):

- `CHANGELOG.md` — история изменений.
- `../docs/ARCHITECTURE_INDEX.md` — канонический обзор экосистемы (точка входа).
- `../docs/CURRENT_STATE.md` — актуальное состояние, миграции, тесты, приоритеты.
- `../docs/PRIORITIES.md` — приоритеты и guardrails.
- `../docs/drun/DRUN_OVERVIEW.md` — Тёмный друн (AI-персона); детали — `DRUN_INTERNALS.md`.
- `../docs/features/*` — подсистемы (экономика, геймплей, сайт, админка, mini-app, модерация).

> Источник истины — код (`app/settings/*.py` для экономики/рангов, миграции
> Alembic для схемы). Исторические отчёты — в `../docs/archive/`.
