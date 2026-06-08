# MINI APP FOUNDATION — проект и оценка работ

Статус: **проект (design only).** Реализации нет. Магазин не проектируется здесь
(он отдельно, см. `FOUNDATION_STATUS.md` §5). Цель документа — полный flow
Telegram Mini App поверх существующей авторизации и честная оценка объёма.

Дата: 2026-06-06
Связанные документы: `FOUNDATION_STATUS.md` (аудит auth, схемы таблиц).

---

## 1. Анализ текущей архитектуры (то, на что опираемся)

### Что уже есть и работает
| Слой | Файл | Роль |
|------|------|------|
| Проверка initData | `v0-voznya/lib/auth/telegram.ts` → `verifyWebAppInitData` | **Уже написана**, по докам Telegram WebApp (`secret = HMAC_SHA256("WebAppData", token)`), timing-safe, проверка свежести `auth_date`. Не подключена ни к одному роуту. |
| Сессия | `lib/auth/session.ts` | Stateless JWT (HS256, `jose`) в httpOnly-cookie `voznya_session`, TTL 30 дней. Payload `{ uid, username, firstName }`. |
| Чтение сессии | `lib/auth/get-session.ts` → `getSession()` | Читает cookie в Server Component / Route Handler. |
| Привязка | `account_links` (PK `oidc_sub`, UNIQUE `user_id`) | Биекция `oidc_sub ↔ user_id`. |
| Read-only API | `app/api/me`, `me/summary`, `profile/[id]`, `economy`, `top-rich`, `top-weekly`, `achievements`, `daily`, `families`, `messages`, `stats`, `commands` | Все читают игровые таблицы, используют `getSession()` для приватных данных. |
| Запросы | `lib/queries.ts` | `getPlayerProfile`, `getUserSummary`, … (read-only). |
| UI | `components/ui/*` (Radix), `components/profile/*`, `components/voznya/*` | Карточки, рейтинги, графики, дровер, тосты. |

### Ключевое отличие Mini App от текущих потоков
В классическом Login Widget и OIDC реальный `user_id` приходится **выводить**:
- Login Widget: `id` уже настоящий Telegram id.
- OIDC: `sub` — непрозрачный, реальный `user_id` берётся из `account_links`
  (поэтому нужен deep-link через бота при первом входе).

**Mini App проще обоих:** `initData.user.id` — это УЖЕ настоящий Telegram
`user_id`. Значит:
- **привязка через бота НЕ нужна** для входа в Mini App;
- `account_links` для Mini App-сессии не задействуется вообще;
- первый вход = повторный вход (нет ветки «создать заявку»).

Это сильно упрощает flow по сравнению с OIDC.

### Главная техническая проблема, которую надо решить в проекте
Mini App работает во **встроенном WebView Telegram** (iOS/Android/Desktop).
Текущая сессия — cookie с `SameSite=Lax`. В WebView/iframe Telegram cookie
с `Lax` **могут не отдаваться** (особенно iOS), потому что контекст
кросс-сайтовый. Полагаться на `Lax`-cookie внутри Mini App нельзя.

Два варианта решения (см. §4) — выбран **Bearer-токен**, как более надёжный в
WebView, с cookie как опциональным дополнением.

---

## 2. Проектируемый Mini App flow (автологин внутри Telegram)

```
┌─────────────────────────── Telegram client (iOS/Android/Desktop) ──────────┐
│  Пользователь жмёт кнопку меню бота / inline-кнопку "Открыть Возню"         │
│                         │                                                   │
│                         ▼                                                   │
│   Telegram открывает WebView с URL Mini App: https://voznya.nl/app          │
│   и инжектит window.Telegram.WebApp (+ initData)                            │
└─────────────────────────┬───────────────────────────────────────────────────┘
                          │ (1) клиент читает Telegram.WebApp.initData (raw string)
                          ▼
        POST /api/auth/telegram-webapp   { initData }      ◄── НОВЫЙ роут
                          │
                          │ (2) verifyWebAppInitData(initData, BOT_TOKEN)
                          │     • HMAC по "WebAppData"
                          │     • timing-safe сравнение hash
                          │     • свежесть auth_date (≤ 1 час)
                          │     → возвращает настоящий userId
                          ▼
        (3) createSessionToken({ uid: userId, ... })   ◄── переиспользуем session.ts
                          │
                          │ ответ: { token }  (+ Set-Cookie SameSite=None; Secure — опционально)
                          ▼
        (4) клиент сохраняет token (in-memory + sessionStorage)
                          │
                          ▼
        (5) запросы к /api/* с заголовком  Authorization: Bearer <token>
                          │
                          ▼
        (6) getSession() читает Bearer ИЛИ cookie → uid → данные игрока
```

Автологин: пользователь НЕ видит никакой кнопки входа. WebView открылся → клиент
сам отправил initData → получил сессию → сразу показал профиль. Ноль кликов.

### Edge cases flow
- **Открыли Mini App вне Telegram** (прямой заход на `/app` в браузере):
  `window.Telegram.WebApp.initData` пустой → показываем обычный экран входа
  (существующая кнопка `TelegramLoginButton`) или сообщение «откройте через
  бота». Не падаем.
- **initData протух** (> 1 часа, редко — Telegram обычно свежий): 401 →
  клиент переинициализирует (`Telegram.WebApp` отдаёт свежий initData при
  перезапуске Mini App).
- **Пользователь не играл** (`userId` не в `users`): сессия выдаётся (вход — про
  Telegram-личность, не про игровой аккаунт), а API профиля честно отдаёт
  «ещё не зарегистрирован», как сейчас на сайте.
- **Подделка initData**: невозможно без `BOT_TOKEN` — HMAC не сойдётся.

---

## 3. Какие файлы уже готовы (переиспользуем как есть)

| Файл | Используется в Mini App | Меняется? |
|------|-------------------------|-----------|
| `lib/auth/telegram.ts` (`verifyWebAppInitData`) | проверка initData в новом роуте | **нет** |
| `lib/auth/session.ts` (`createSessionToken`, `verifySessionToken`) | выпуск/проверка JWT | **минимально** (см. §4 — добавить парсер Bearer) |
| `lib/auth/get-session.ts` (`getSession`) | чтение сессии в API | **да** — научить читать Bearer, не только cookie |
| `lib/db.ts`, `lib/queries.ts` | данные игрока | **нет** |
| Все `app/api/*` read-only роуты | контент Mini App | **нет логики**, но начнут понимать Bearer автоматически через `getSession` |
| `components/ui/*`, `components/profile/*` | экраны Mini App | **нет** (переиспользуем) |
| `hooks/use-api.ts` | загрузка данных | **минимально** — добавить заголовок Authorization |

Вывод: ~80% инфраструктуры готово. Проверка подписи и сессии — самое сложное —
**уже написаны**.

---

## 4. Какие новые файлы потребуются (сайт)

> Объёмы — грубая оценка строк, чтобы прикинуть масштаб.

### 4.1 Бэкенд (Next.js route handlers)
| Файл | Назначение | ~Строк |
|------|-----------|--------|
| `app/api/auth/telegram-webapp/route.ts` | **Главный новый роут.** `POST { initData }` → `verifyWebAppInitData` → `createSessionToken` → `{ token }` + опц. `Set-Cookie SameSite=None; Secure`. Образец — `app/api/auth/telegram/route.ts`. | ~50 |
| `lib/auth/bearer.ts` (или правка `get-session.ts`) | Достать `Authorization: Bearer <jwt>` из заголовков, провалидировать через `verifySessionToken`. `getSession()` сначала пробует Bearer, потом cookie. | ~25 |

### 4.2 Фронтенд (Mini App UI)
| Файл | Назначение | ~Строк |
|------|-----------|--------|
| `app/app/layout.tsx` | Отдельный layout Mini App: подключение Telegram WebApp SDK, тема из `themeParams`, фикс. вьюпорт, без сайтового хедера. | ~60 |
| `app/app/page.tsx` | Главный экран Mini App (профиль/дашборд игрока). Собирается из существующих компонентов. | ~80 |
| `lib/telegram/webapp.ts` | Клиентский хелпер: типобезопасный доступ к `window.Telegram.WebApp`, `ready()`, `expand()`, `initData`, `themeParams`, `MainButton`, `HapticFeedback`. | ~80 |
| `hooks/use-tma-session.ts` | Хук автологина: на маунте читает initData → POST на новый роут → хранит токен (in-memory + `sessionStorage`) → отдаёт `{ uid, loading, error }`. | ~70 |
| `components/tma/tma-provider.tsx` | Контекст: токен + данные сессии для дерева Mini App; обёртка `fetch` с `Authorization`. | ~60 |
| `components/tma/auth-gate.tsx` | Показывает спиннер во время автологина, fallback-экран вне Telegram. | ~40 |
| `public/` или `<Script>` | Подключение `https://telegram.org/js/telegram-web-app.js`. | ~5 |

### 4.3 Конфигурация
| Файл | Изменение |
|------|-----------|
| `.env.example` | Документировать, что Mini App использует тот же `TELEGRAM_BOT_TOKEN` и `AUTH_SECRET`. Новых секретов не требуется. |
| `next.config.mjs` | Возможно `headers()` для нужных CSP/`frame-ancestors` Telegram (если включён CSP). Проверить. |

**Итого новых файлов: ~9, ~500 строк.** Большая часть — UI-обвязка, не логика.

---

## 5. Какие изменения нужны в боте (voznya-bot)

Бот в Mini App-флоу **почти не участвует** (в отличие от OIDC, где он
подтверждает привязку). Нужно лишь дать точку входа в Mini App:

| Изменение | Файл | ~Строк |
|-----------|------|--------|
| Кнопка-меню «Открыть Возню» (chat menu button → Web App) | новый код в `on_startup` (`app/main.py`) через `bot.set_chat_menu_button(MenuButtonWebApp(...))` | ~10 |
| (Опц.) inline-кнопка `WebAppInfo(url=...)` в `/help` или новой команде `/app` | `app/features/help/handlers.py` или новый `app/features/miniapp/` | ~20 |
| Настройка `MINIAPP_URL` | `app/config.py` (+ `.env.example`) | ~3 |

Важно:
- Кнопка Web App в **меню бота** работает только в приватном чате — это норма.
- Inline-кнопки `WebAppInfo` в группах требуют HTTPS-URL (есть: `voznya.nl`).
- Бот **не выпускает сессию и не читает initData** — это делает сайт. Бот лишь
  открывает WebView с правильным URL.
- `account_links` и `oidc_link_requests` Mini App **не трогает**.

**Итого по боту: ~3 правки, ~35 строк.** Минимально.

---

## 6. Что потребуется в BotFather

| Шаг | Зачем |
|-----|-------|
| `/newapp` (или `/myapps`) → выбрать бота → создать Mini App | Зарегистрировать Mini App, задать его URL (`https://voznya.nl/app`), название, иконку, короткое имя (для `t.me/<bot>/<shortname>`). |
| `/setmenubutton` → URL = `https://voznya.nl/app` | Кнопка-меню в чате бота открывает Mini App (альтернатива установке из кода через `set_chat_menu_button`). |
| `/setdomain` (если ещё не стоит) | Должен указывать на `voznya.nl` — иначе Telegram не доверит WebView/Login. Скорее всего уже настроено для OIDC. |
| (Опц.) Direct Link | `t.me/<bot>/<shortname>` как прямая ссылка на Mini App для шеринга. |

Новых токенов/секретов BotFather для Mini App **не выдаёт** — initData
подписывается тем же `BOT_TOKEN`, что уже есть. Это удобно: один секрет на бота
и сайт.

---

## 7. Как выглядит автологин внутри Telegram (UX)

1. Пользователь в чате с ботом жмёт кнопку меню (или `/app`, или Direct Link).
2. Открывается WebView Возни на весь экран Telegram. Виден спиннер ~0.3–1 с.
3. Клиент молча отправил initData, получил сессию — **без единого клика и без
   экрана входа**. Сразу показывается профиль/дашборд.
4. Тема (тёмная/светлая) подхватывается из Telegram (`themeParams`), Mini App
   выглядит «родным».
5. Кнопки (например, будущая «Купить» для магазина) — через нативную
   `MainButton` Telegram + `HapticFeedback`.

Контраст с текущим веб-входом: на сайте нужен клик «Войти через Telegram» и для
OIDC-новичков ещё переход в бота. В Mini App — **ноль шагов**, потому что
Telegram уже знает, кто пользователь, и подписывает это в initData.

---

## 8. Стратегия сессии: почему Bearer, а не cookie (важное решение)

| Критерий | Cookie `SameSite=None; Secure` | **Bearer-токен (выбран)** |
|----------|-------------------------------|---------------------------|
| Надёжность в WebView Telegram (iOS) | ненадёжно: iOS режет сторонние cookie | надёжно: токен в памяти/sessionStorage, шлётся в заголовке |
| Изменения в API-роутах | нет (cookie читается как сейчас) | да: `getSession` должен понимать Bearer |
| Совместимость с обычным сайтом | возможны побочки (None ослабляет CSRF-защиту глобально) | сайт не трогаем — cookie остаётся `Lax` |
| Сложность | ниже, но хрупко | чуть выше, но устойчиво |

**Решение:** Mini App использует **Bearer-токен** (тот же JWT из `session.ts`,
просто доставляется в заголовке, а не в cookie). `getSession()` дорабатывается,
чтобы принимать оба источника: сначала `Authorization: Bearer`, потом cookie.
Это:
- не ослабляет CSRF-защиту обычного сайта (cookie остаётся `Lax`);
- работает во всех клиентах Telegram;
- переиспользует ровно тот же механизм подписи/проверки JWT.

Опционально новый роут может ВДОБАВОК ставить `SameSite=None; Secure` cookie —
как прогрессивное улучшение для клиентов, где cookie работает (Desktop). Но
основной канал — Bearer.

### Замечание по безопасности
- initData проверяется на сервере (HMAC + свежесть) — подделать нельзя.
- JWT остаётся коротко-понятным (`uid`); срок можно сделать короче для Mini App
  (например 7 дней вместо 30), т.к. переавторизация бесплатна (initData всегда
  под рукой). Это решается параметром при `createSessionToken`.
- `sessionStorage` (не `localStorage`) — токен живёт в рамках сессии WebView,
  не утекает между запусками. Достаточно, т.к. автологин мгновенный.
- Bearer-токен в заголовке не подвержен CSRF (в отличие от cookie).

---

## 9. Оценка объёма работ

| Блок | Файлы | Сложность | Оценка |
|------|-------|-----------|--------|
| Auth-роут `telegram-webapp` | 1 новый | низкая (есть `verifyWebAppInitData`) | 0.5 дня |
| Bearer в `getSession` | 1 правка | низкая | 0.25 дня |
| Клиентский TMA-слой (SDK-хелпер, хук, провайдер, gate) | 4 новых | средняя | 1.5 дня |
| Layout + главный экран Mini App | 2 новых | средняя (переиспользуем компоненты) | 1 день |
| Правки в боте (menu button, /app, конфиг) | 3 правки | низкая | 0.5 дня |
| BotFather + тема/вьюпорт/тестирование на устройствах | — | средняя (нужны реальные iOS/Android) | 1 день |
| **Итого фундамент Mini App (без магазина)** | ~9 новых + ~5 правок | — | **~4.5–5 дней** |

Риски, способные сдвинуть оценку:
- Поведение cookie/CSP в конкретных версиях клиентов Telegram → закладываем
  Bearer, поэтому риск низкий.
- Тестирование Mini App требует HTTPS-домена и реального Telegram (локально —
  только через `ngrok`/туннель + BotFather test app). Заложен 1 день на отладку.
- `next.config.mjs`/CSP `frame-ancestors` если включён строгий CSP — проверить
  заранее.

---

## 10. Порядок реализации (когда дадут добро)

1. **Бэкенд-вход** — `app/api/auth/telegram-webapp/route.ts` + Bearer в
   `getSession`. Проверить curl-ом (initData можно получить из тестового Mini
   App).
2. **Клиентский слой** — `lib/telegram/webapp.ts`, `use-tma-session.ts`,
   `tma-provider.tsx`, `auth-gate.tsx`.
3. **Экран** — `app/app/layout.tsx` + `app/app/page.tsx` из готовых компонентов.
4. **Бот** — menu button + `/app` + `MINIAPP_URL`.
5. **BotFather** — `/newapp`, `/setmenubutton`, проверить `/setdomain`.
6. **Тест на устройствах** — iOS, Android, Desktop; тема, автологин, протухший
   initData, заход вне Telegram.

Магазин (write-API, таблицы из `FOUNDATION_STATUS.md` §5) — **отдельный этап
после** этого фундамента.

---

## 11. Что НЕ входит в этот проект
- ❌ Реализация (только проект и оценка).
- ❌ Магазин, корзина, покупки, write-API.
- ❌ Подарки.
- ❌ Новые игровые механики.
- ❌ Изменение OIDC/Login Widget потоков (они остаются как есть).
