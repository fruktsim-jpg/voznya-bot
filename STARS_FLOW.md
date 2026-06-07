# Stars: полный путь от оплаты до баланса бота + P&L

Минимальный топ-ап реализован (по требованию — без донат-магазина). Это фундамент
будущего «донат Stars→ешки» на тех же точках. Сверено с Bot API
(STARS_FUNDING_GUIDE). Источник правды по Stars — таблица ``stars_ledger``.

---

## 1. Полный путь Stars (от оплаты до баланса бота)

```
Админ:  /topup 50
  │
  ▼
бот: send_invoice(currency='XTR', provider_token='', prices=[50★], payload='topup:50')
  │                                   (app/features/payments/handlers.cmd_topup)
  ▼
Telegram показывает счёт → админ оплачивает своими Stars
  │
  ▼
Telegram → pre_checkout_query        → бот: answer(ok=True)   (on_pre_checkout)
  │
  ▼
Telegram списывает Stars у плательщика, зачисляет боту
  │
  ▼
Telegram → message.successful_payment(total_amount=50,
                                       telegram_payment_charge_id=...)
  │                                   (on_successful_payment)
  ├─ getMyStarBalance()  → баланс бота после зачисления (balance_after)
  ├─ stars.record_in(direction='in', reason='topup', amount=50,
  │                   charge_id=..., balance_after=...)  → строка stars_ledger
  │                   (идемпотентно: charge_id UNIQUE — повтор не задвоит)
  ▼
Ответ админу: «Зачислено 50 ⭐. Баланс бота: <balance>».
```

Расход (выдача Gift) — зеркально:
```
deliver_gift → send_gift(sendGift) → True
  └─ stars.record_out(direction='out', reason='gift_send', amount=star_cost,
                      ref=idempotency_key, balance_after=...)  → строка stars_ledger
```

Итого баланс бота отражён в двух местах:
- внешний (правда Telegram) — `getMyStarBalance`;
- наш (бизнес-история) — `stars_ledger`: `Σ in − Σ out`. Их сверка = reconcile.

## 2. Где это в коде

| Шаг | Файл |
|---|---|
| Команда `/topup`, инвойс | `app/features/payments/handlers.py` (`cmd_topup`) |
| Пред-чек | то же (`on_pre_checkout`) — отвечает ok=True |
| Приём платежа | то же (`on_successful_payment`) → `stars.record_in` |
| Запись в леджер | `app/services/stars.py` (`record_in` / `record_out`) |
| Таблица | `app/models/stars_ledger.py`, миграция `0022_stars_ledger` |
| Баланс бота | `app/services/telegram_gifts.get_star_balance` |
| Расход при выдаче Gift | `app/features/gifts/service.deliver_gift` → `stars.record_out` |

Регистрация роутера `payments` — рано в `app/features/__init__.py` (чтобы
`pre_checkout_query`/`successful_payment` гарантированно ловились).

## 3. Логирование (требуемый набор — весь есть)

`stars_ledger` на каждую операцию: `user_id`, `amount_stars`, `direction`,
`reason`, `charge_id` (для приходов, UNIQUE), `ref` (для расходов = idempotency_key
доставки), `source` (bot/site/miniapp), `balance_after`, `created_at`, `meta`
(payload, currency, provider_charge_id и т.п.). Через полгода любой топ-ап/расход
полностью восстановим, входящие платежи дедуплицированы.

## 4. Как это попадает в аналитику (Economic Control Center)

`lib/economy-analytics.loadGiftsOverview` дополнительно агрегирует `stars_ledger`:
- `starsIn` = Σ `amount_stars` где `direction='in'` (топ-апы, позже донаты);
- `starsOut` = Σ `amount_stars` где `direction='out'` (расход на подарки);
- `fundBalance` = `starsIn − starsOut` (наши книги; `null` только если таблицы ещё нет).

На странице `/admin/economy/gifts` карточка «Баланс фонда Stars» теперь реальная
(с подписью «пополнено / истрачено ⭐»). Деградация безопасна: нет таблицы → `—`.

## 5. Как считать P&L

### По Gifts (в ешках — игровая выручка vs себестоимость)
- **Выручка**: Σ `purchase_history.price` (`source='gift'`, без возвратов) — ешки,
  которые игроки реально отдали за подарки.
- **Себестоимость**: Σ `star_cost` по выданным (`gift_transactions.status='completed'`)
  × 10 (курс 1★≈10 ешек из VOZNYA_ECONOMY_V2) — в ешко-эквиваленте.
- **Маржа** = выручка − себестоимость×10. Уже считается (`marginEshki`).

### По Stars (в звёздах — денежный фонд)
- **Приход Stars**: `starsIn` (топ-апы; позже донаты) — сколько Stars влилось.
- **Расход Stars**: `starsOut` (`reason='gift_send'`) — сколько ушло на подарки.
- **Баланс фонда** = `starsIn − starsOut` (сверяется с `getMyStarBalance`).
- **Реальная стоимость подарков в Stars**: `starsSpentRealized` (по completed).

### Связка двух валют (общая картина прибыльности)
- Игроки тратят **ешки** (внутренние, бесплатно намайненные) → это НЕ денежный
  доход, а игровой сток.
- Бот тратит **Stars** (реальные деньги владельца) на выдачу.
- Поэтому «денежный» P&L = сколько Stars влилось (донаты/топ-апы) минус сколько
  потрачено на подарки = `fundBalance`. Если фонд уходит в минус — подарки дотируются
  владельцем; плюс — донаты перекрывают расходы. Это и есть метрика устойчивости.

### Будущее (донат Stars→ешки) — без переделок
Когда включим донат: тот же `on_successful_payment` по `payload` distinguish
`donation`, пишет `stars.record_in(reason='donation')` **и** начисляет игроку ешки
через `economy.change_balance`. Тогда `starsIn` начнёт включать донаты, а связь
«Stars влились → ешки выданы» будет видна по двум леджерам с общим `charge_id`/meta.

---

## 6. Runbook: первый реальный top-up → первая выдача Gift

На VPS (здесь нет docker/node в PATH):
```bash
docker compose exec bot alembic upgrade head        # дойдёт до 0022_stars_ledger
docker compose exec bot pytest -q                   # smoke
```
1. В личке с ботом (как админ): `/topup 50`. Откроется счёт.
2. Оплатить счёт своими Stars (нужно заранее купить Stars в приложении Telegram).
3. Бот ответит «Зачислено 50 ⭐. Баланс бота: …». Проверка:
   ```bash
   docker compose exec db psql -U voznya -d voznya -c \
     "SELECT direction,reason,amount_stars,charge_id,balance_after,created_at FROM stars_ledger ORDER BY id DESC LIMIT 5;"
   ```
4. Проставить реальный `telegram_gift_id` тестовой позиции каталога (иначе выдача
   не пройдёт — сид кладёт NULL).
5. `.env`: `GIFTS_DELIVERY_ENABLED=true`, `docker compose up -d`.
6. `/подарки` → купить дешёвый подарок (≤ баланса). Ожидаемо: «Подарок отправлен»,
   `gift_transactions.status='completed'`, в `stars_ledger` строка `out/gift_send`.
7. Открыть `/admin/economy/gifts`: «Баланс фонда Stars» уменьшился на star_cost,
   «Истрачено Stars (факт)» и «выдано» выросли.

Откат: `GIFTS_DELIVERY_ENABLED=false` → покупки снова копятся в pending, Stars не
тратятся.
