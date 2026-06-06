# AGENTS.md

Guidance for AI agents working in this repository.

## Project status

`voznya-bot` is an **active project**, not a stub. It is the Telegram bot half of
the «Возня» ecosystem: a Python (aiogram) bot backed by PostgreSQL with Alembic
migrations. The companion repo `v0-voznya` (Next.js) is the website + admin panel.

Before working, read `README.md` and `PROJECT_STATE_REPORT.md` — they describe the
real, current state. For per-subsystem detail see the `*_FOUNDATION.md`,
`ADMIN_PLATFORM.md`, `COMBOT_IMPORT_PLAN.md`, `MINI_APP_PLAN.md`, `docs/ECONOMY.md`.
Stale one-off reports live in `docs/archive/` — do not treat them as current.

## Stack & layout

- Language/runtime: Python 3.12, aiogram. Deps in `requirements.txt`.
- DB: PostgreSQL, migrations via Alembic (`alembic.ini`, `migrations/`).
- Container: `Dockerfile` + `docker-compose.yml` (bot + db).
- Code: `app/` — `features/` (bot routers), `repositories/`, `services/`,
  `models/`, `core/`, `settings/`, `middlewares/`, `config.py`, `main.py`.

## Source of truth

- The bot is the **only** writer of `users` and gameplay tables.
- The website is **read-only** over `users`, writing only via explicit admin
  routes (`/api/admin/*`) and the OIDC linking flow. Do not introduce website
  writes that bypass the bot.

## Build / run / migrate

```bash
cp .env.example .env
docker compose up -d
docker compose exec bot alembic upgrade head     # apply migrations
docker compose logs -f bot
```

Local (no Docker): `pip install -r requirements.txt`, set `DATABASE_URL`,
`alembic upgrade head`, `python -m app.main`.

Migration chain is linear; current HEAD is `0014_mmr_foundation`. Add new
revisions on top — never rewrite past migrations. No automated test suite exists
yet; if you add tests, use pytest as the standard choice.

## What is implemented vs foundation-only

- Implemented & live: farm, duel, treasure, casino, marriage/para, pidor,
  achievements, economy/transactions, ratings, profile, help, reputation, MMR,
  OIDC linking, admin platform (bot RBAC + site UI + audit), Combot import.
- Foundation-only (schema + migration + doc, NO runtime code): inventory, shop,
  gifts, cosmetics, Mini App. Do not assume these work.

## Git

- Default branch: `main`. Remote: `origin` → GitHub `fruktsim-jpg/voznya-bot`.
- Detached HEAD checkouts should be switched to `main` before branching:
  `git checkout main`.

## Gotchas

- MMR ranks are duplicated in `app/settings/mmr.py` (`RANKS`) and
  `v0-voznya/lib/queries.ts` (`MMR_RANKS`) — keep them in sync if changed.
- Reputation/MMR site blocks are hidden until migrations 0013/0014 are applied
  on prod.
- Do not touch the economy core (`app/core/economy_events.py`, `transactions`),
  account_links/OIDC, or past migrations without explicit need and tests.
