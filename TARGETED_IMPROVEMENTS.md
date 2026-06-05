# 🎯 ТОЧЕЧНЫЕ УЛУЧШЕНИЯ БОТА ВОЗНЯ

**Дата:** 5 июня 2026, 02:35 UTC+2  
**Статус:** 📋 СПИСОК ИЗМЕНЕНИЙ

---

## 🎯 ЦЕЛЬ

Минимальные точечные изменения для единого ощущения бота без переписывания существующих команд.

---

## 📋 СПИСОК КОНКРЕТНЫХ ИЗМЕНЕНИЙ

### 1. ПРОФИЛЬ `/profile`

**Файл:** `app/features/profile/handlers.py`

**Проблема:** Не видно чей профиль открыт

**Изменение:**
```python
# БЫЛО:
text = (
    f"👤 <b>Профиль игрока</b>\n\n"
    f"💰 Баланс: <b>{user.balance:,}</b> ешек\n"
    ...
)

# СТАЛО:
text = (
    f"👤 <b>Профиль — {user.display_name()}</b>\n\n"
    f"💰 Баланс: <b>{user.balance:,}</b> ешек\n"
    ...
)
```

**Результат:** Сразу видно чей профиль

---

### 2. БАЛАНС `/balance`

**Файл:** `app/features/balance/handlers.py`

**Проблема 1:** Не показывает место в топе  
**Проблема 2:** Удаляет сообщение пользователя

**Изменение 1 - добавить место в топе:**
```python
# Добавить после получения record:
rank = await users_repo.get_user_rank_by_balance(session, user.id)

# Изменить текст:
sent = await message.answer(
    texts.BALANCE.format(
        mention=mention(user.id, user.first_name, user.username),
        balance=money(amount),
        title=get_title(earned).label,
    ) + (f"\n🏆 Место в топе: #{rank}" if rank else "")
)
```

**Изменение 2 - не удалять сообщение пользователя:**
```python
# УБРАТЬ эту строку:
await deletion.schedule(session, message.chat.id, message.message_id, 5)
```

**Результат:** Показывает место в топе, не удаляет команду пользователя

---

### 3. ДОСТИЖЕНИЯ `/achievements`

**Файл:** `app/features/achievements/handlers.py`

**Проблема:** Удаляет сообщение пользователя

**Изменение:**
```python
# УБРАТЬ эту строку:
await deletion.schedule(session, message.chat.id, message.message_id, 5)
```

**Результат:** Не удаляет команду пользователя

---

### 4. РЕЙТИНГИ `/top`, `/weekly`, `/families`

**Файл:** `app/features/ratings/handlers.py`

**Проблема:** Удаляет сообщение пользователя

**Изменение во всех трёх командах:**
```python
# УБРАТЬ эту строку:
await deletion.schedule(session, message.chat.id, message.message_id, 5)
```

**Результат:** Не удаляет команду пользователя

---

### 5. БРАК `/marriage`

**Файл:** `app/features/marriage/handlers.py`

**Проблема:** Слишком точное время (часы и минуты не нужны)

**Изменение в `app/core/utils.py`:**
```python
# Добавить новую функцию:
def format_marriage_duration_days(since: datetime, until: datetime | None = None) -> str:
    """Форматирует длительность брака только в днях."""
    start = to_local(since)
    end = to_local(until) if until is not None else now_local()
    delta = end - start
    if delta.total_seconds() < 0:
        delta = timedelta(0)
    days = delta.days
    return f"{days} дн." if days != 0 else "менее дня"
```

**Изменение в handlers.py:**
```python
# БЫЛО:
duration=format_marriage_duration(marriage.married_at)

# СТАЛО:
duration=format_marriage_duration_days(marriage.married_at)
```

**Результат:** Показывает только дни, без часов и минут

---

### 6. ТОП СЕМЕЙ `/families`

**Файл:** `app/features/ratings/handlers.py`

**Проблема:** Слишком точное время

**Изменение:**
```python
# БЫЛО:
duration=format_marriage_duration(m.married_at)

# СТАЛО:
duration=format_marriage_duration_days(m.married_at)
```

**Результат:** Показывает только дни

---

### 7. НАВИГАЦИЯ - Добавить кнопки

**Файл:** `app/core/keyboards.py`

**Добавить новые функции:**

```python
def profile_navigation() -> InlineKeyboardMarkup:
    """Кнопки навигации для профиля."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Баланс", callback_data="quick:balance")
    builder.button(text="🏅 Ачивки", callback_data="quick:achievements")
    builder.adjust(2)
    return builder.as_markup()

def balance_navigation() -> InlineKeyboardMarkup:
    """Кнопки навигации для баланса."""
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Профиль", callback_data="quick:profile")
    builder.button(text="🏆 Топ", callback_data="quick:top")
    builder.adjust(2)
    return builder.as_markup()
```

**Файл:** `app/features/quick/handlers.py`

**Добавить обработчик для топа:**

```python
@router.callback_query(F.data == "quick:top")
async def q_top(callback: CallbackQuery, session: AsyncSession) -> None:
    """Быстрый топ."""
    from app.features.ratings.handlers import render_top
    
    user_id = callback.from_user.id if callback.from_user else None
    text, total_pages = await render_top(session, page=1, user_id=user_id)
    
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=quick_actions())
        except Exception:
            await callback.message.answer(text, reply_markup=quick_actions())
    await callback.answer()
```

**Использование:**

В `app/features/profile/handlers.py`:
```python
# Заменить:
await message.answer(text, reply_markup=keyboard)

# На:
from app.core.keyboards import profile_navigation
# ... в конце функции добавить кнопки навигации под кнопкой сайта
```

В `app/features/balance/handlers.py`:
```python
# Добавить:
from app.core.keyboards import balance_navigation
sent = await message.answer(text, reply_markup=balance_navigation())
```

---

## 📊 ИТОГОВАЯ ТАБЛИЦА ИЗМЕНЕНИЙ

| Файл | Изменение | Строки |
|------|-----------|--------|
| `app/features/profile/handlers.py` | Добавить имя в заголовок | ~1 |
| `app/features/profile/handlers.py` | Добавить кнопки навигации | ~2 |
| `app/features/balance/handlers.py` | Добавить место в топе | ~3 |
| `app/features/balance/handlers.py` | Убрать удаление сообщения | -1 |
| `app/features/balance/handlers.py` | Добавить кнопки навигации | ~2 |
| `app/features/achievements/handlers.py` | Убрать удаление сообщения | -1 |
| `app/features/ratings/handlers.py` | Убрать удаление сообщений (×3) | -3 |
| `app/features/ratings/handlers.py` | Упростить время браков | ~1 |
| `app/features/marriage/handlers.py` | Упростить время брака | ~1 |
| `app/core/utils.py` | Добавить функцию форматирования дней | ~10 |
| `app/core/keyboards.py` | Добавить функции навигации | ~20 |
| `app/features/quick/handlers.py` | Добавить обработчик топа | ~15 |

**Всего:**
- Файлов: 7
- Добавлено строк: ~55
- Удалено строк: ~5
- Изменено строк: ~5

---

## 📝 ПРИМЕРЫ СООБЩЕНИЙ ДО/ПОСЛЕ

### ПРОФИЛЬ

#### ДО:
```
👤 Профиль игрока

💰 Баланс: 1,234 ешек
📈 Заработано: 5,678 ешек
🏆 Титул: 💊 Аптекарь
📊 Прогресс: 40% до 🌿 Травник
🏅 Достижения: 6/27
💍 В браке с Мария
⚔️ Дуэли: 12 побед / 3 поражений
🌾 Серия фермы: 5 (рекорд: 12)
📦 Кладов найдено: 8

Полная статистика на сайте: https://voznya.nl/profile/123456

[🌐 Открыть профиль на сайте]
```

#### ПОСЛЕ:
```
👤 Профиль — Андрей

💰 Баланс: 1,234 ешек
📈 Заработано: 5,678 ешек
🏆 Титул: 💊 Аптекарь
📊 Прогресс: 40% до 🌿 Травник
🏅 Достижения: 6/27
💍 В браке с Мария
⚔️ Дуэли: 12 побед / 3 поражений
🌾 Серия фермы: 5 (рекорд: 12)
📦 Кладов найдено: 8

Полная статистика на сайте: https://voznya.nl/profile/123456

[🌐 Открыть профиль на сайте]
[💰 Баланс] [🏅 Ачивки]
```

**Изменения:**
- ✅ Добавлено имя в заголовок
- ✅ Добавлены кнопки навигации

---

### БАЛАНС

#### ДО:
```
💰 Андрей: 1,234 ешки · 💊 Аптекарь
```
*(Сообщение пользователя "/баланс" удаляется через 5 сек)*

#### ПОСЛЕ:
```
💰 Андрей: 1,234 ешки · 💊 Аптекарь
🏆 Место в топе: #17

[👤 Профиль] [🏆 Топ]
```
*(Сообщение пользователя "/баланс" НЕ удаляется)*

**Изменения:**
- ✅ Добавлено место в топе
- ✅ Добавлены кнопки навигации
- ✅ Не удаляется сообщение пользователя

---

### БРАК

#### ДО:
```
💍 Андрей ❤️ Мария · вместе 12 дн. 5 ч. 30 мин.
```

#### ПОСЛЕ:
```
💍 Андрей ❤️ Мария · вместе 12 дн.
```

**Изменения:**
- ✅ Упрощено время (только дни)

---

### ТОП СЕМЕЙ

#### ДО:
```
💞 Крепкие семьи

🥇 Игрок1 ❤️ Игрок2 — 45 дн. 12 ч. 30 мин.
🥈 Игрок3 ❤️ Игрок4 — 38 дн. 8 ч. 15 мин.
🥉 Игрок5 ❤️ Игрок6 — 32 дн. 5 ч. 45 мин.
```
*(Сообщение пользователя "/семьи" удаляется через 5 сек)*

#### ПОСЛЕ:
```
💞 Крепкие семьи

🥇 Игрок1 ❤️ Игрок2 — 45 дн.
🥈 Игрок3 ❤️ Игрок4 — 38 дн.
🥉 Игрок5 ❤️ Игрок6 — 32 дн.
```
*(Сообщение пользователя "/семьи" НЕ удаляется)*

**Изменения:**
- ✅ Упрощено время (только дни)
- ✅ Не удаляется сообщение пользователя

---

### ДОСТИЖЕНИЯ

#### ДО:
```
🏆 ДОСТИЖЕНИЯ
👤 Андрей · 6/27

✅ 💰 Первая ешка · +10
✅ 💊 Аптекарь · +50
✅ 🌾 Фермер · +100
✅ ⚔️ Боец · +75
✅ 📦 Кладоискатель · +50
✅ 🎰 Лудоман · +25

[📖 Все достижения]
```
*(Сообщение пользователя "/ачивки" удаляется через 5 сек)*

#### ПОСЛЕ:
```
🏆 ДОСТИЖЕНИЯ
👤 Андрей · 6/27

✅ 💰 Первая ешка · +10
✅ 💊 Аптекарь · +50
✅ 🌾 Фермер · +100
✅ ⚔️ Боец · +75
✅ 📦 Кладоискатель · +50
✅ 🎰 Лудоман · +25

[📖 Все достижения]
```
*(Сообщение пользователя "/ачивки" НЕ удаляется)*

**Изменения:**
- ✅ Не удаляется сообщение пользователя

---

### ТОПЫ

#### ДО:
```
🏆 Богачи Возни

🥇 Игрок1 — 10,000 ешек
🥈 Игрок2 — 8,500 ешек
🥉 Игрок3 — 7,200 ешек
...

📍 Твоё место: #17
```
*(Сообщение пользователя "/топ" удаляется через 5 сек)*

#### ПОСЛЕ:
```
🏆 Богачи Возни

🥇 Игрок1 — 10,000 ешек
🥈 Игрок2 — 8,500 ешек
🥉 Игрок3 — 7,200 ешек
...

📍 Твоё место: #17
```
*(Сообщение пользователя "/топ" НЕ удаляется)*

**Изменения:**
- ✅ Не удаляется сообщение пользователя

---

## ✅ ЧТО НЕ МЕНЯЕТСЯ

- ❌ Структура команд
- ❌ Тексты сообщений (кроме заголовка профиля)
- ❌ Экономика
- ❌ Логика игры
- ❌ База данных
- ❌ Казино
- ❌ Ферма
- ❌ Дуэли
- ❌ Достижения (кроме удаления сообщения)

---

## 🚀 СЛЕДУЮЩИЕ ШАГИ

1. ✅ Показать список изменений
2. ✅ Показать примеры сообщений
3. ⏳ Получить одобрение
4. ⏳ Реализовать изменения
5. ⏳ Показать git diff
6. ⏳ Протестировать

---

**Конец документа**

**Статус:** 📋 Ожидает одобрения
