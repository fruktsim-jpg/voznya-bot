# ✅ ОДОБРЕННЫЕ ИЗМЕНЕНИЯ БОТА ВОЗНЯ

**Дата:** 5 июня 2026, 02:39 UTC+2  
**Статус:** 📋 ГОТОВ К РЕАЛИЗАЦИИ

---

## 🎯 ЦЕЛЬ

Исправить только реальные неудобства, сохранив текущий стиль бота.

---

## ✅ ОДОБРЕННЫЕ ИЗМЕНЕНИЯ

### 1. ПРОФИЛЬ - Добавить имя в заголовок

**Файл:** `app/features/profile/handlers.py`  
**Строка:** ~28

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

**Результат:**
```
👤 Профиль — Андрей

💰 Баланс: 1,234 ешек
📈 Заработано: 5,678 ешек
🏆 Титул: 💊 Аптекарь
...
```

---

### 2. БАЛАНС - Добавить место в топе

**Файл:** `app/features/balance/handlers.py`  
**Строки:** ~27-43

**Изменение:**
```python
# БЫЛО:
record = await users_repo.get_user(session, user.id)
amount = record.balance if record else 0
earned = record.total_earned if record else 0

deletion = get_deletion_service()

# Удаляем команду пользователя через 5 сек
await deletion.schedule(session, message.chat.id, message.message_id, 5)

# Отправляем баланс и удаляем через 2 минуты
sent = await message.answer(
    texts.BALANCE.format(
        mention=mention(user.id, user.first_name, user.username),
        balance=money(amount),
        title=get_title(earned).label,
    )
)
await deletion.schedule(session, sent.chat.id, sent.message_id, 120)

# СТАЛО:
record = await users_repo.get_user(session, user.id)
amount = record.balance if record else 0
earned = record.total_earned if record else 0
rank = await users_repo.get_user_rank_by_balance(session, user.id)

deletion = get_deletion_service()

# Отправляем баланс с местом в топе
balance_text = texts.BALANCE.format(
    mention=mention(user.id, user.first_name, user.username),
    balance=money(amount),
    title=get_title(earned).label,
)
if rank:
    balance_text += f"\n🏆 Место в топе: #{rank}"

sent = await message.answer(balance_text)

# Удаляем через 60 секунд
await deletion.schedule(session, sent.chat.id, sent.message_id, 60)
```

**Результат:**
```
💰 Андрей: 1,234 ешки · 💊 Аптекарь
🏆 Место в топе: #17
```

---

### 3. УПРОСТИТЬ ВРЕМЯ БРАКОВ

**Файл:** `app/core/utils.py`  
**Строка:** ~92 (после format_marriage_duration)

**Добавить новую функцию:**
```python
def format_marriage_duration_days(since: datetime, until: datetime | None = None) -> str:
    """Форматирует длительность брака только в днях."""
    start = to_local(since)
    end = to_local(until) if until is not None else now_local()
    delta = end - start
    if delta.total_seconds() < 0:
        delta = timedelta(0)
    days = delta.days
    return f"{days} дн." if days > 0 else "менее дня"
```

**Файл:** `app/features/marriage/handlers.py`  
**Строка:** ~191

**Изменение:**
```python
# БЫЛО:
from app.core.utils import format_marriage_duration, mention, now_utc

await message.answer(
    texts.MARRIAGE_INFO.format(
        first=await _mention_of(session, marriage.user_id_1),
        second=await _mention_of(session, marriage.user_id_2),
        duration=format_marriage_duration(marriage.married_at),
    )
)

# СТАЛО:
from app.core.utils import format_marriage_duration_days, mention, now_utc

await message.answer(
    texts.MARRIAGE_INFO.format(
        first=await _mention_of(session, marriage.user_id_1),
        second=await _mention_of(session, marriage.user_id_2),
        duration=format_marriage_duration_days(marriage.married_at),
    )
)
```

**Файл:** `app/features/ratings/handlers.py`  
**Строка:** ~168

**Изменение:**
```python
# БЫЛО:
from app.core.utils import format_marriage_duration, mention, place_marker

lines.append(
    texts.TOP_FAMILIES_ROW.format(
        place=place_marker(i + 1),
        first=mention(u1.user_id, u1.first_name, u1.username) if u1 else "?",
        second=mention(u2.user_id, u2.first_name, u2.username) if u2 else "?",
        duration=format_marriage_duration(m.married_at),
    )
)

# СТАЛО:
from app.core.utils import format_marriage_duration_days, mention, place_marker

lines.append(
    texts.TOP_FAMILIES_ROW.format(
        place=place_marker(i + 1),
        first=mention(u1.user_id, u1.first_name, u1.username) if u1 else "?",
        second=mention(u2.user_id, u2.first_name, u2.username) if u2 else "?",
        duration=format_marriage_duration_days(m.married_at),
    )
)
```

**Результат:**
```
💍 Андрей ❤️ Мария · вместе 12 дн.

💞 Крепкие семьи
🥇 Игрок1 ❤️ Игрок2 — 45 дн.
```

---

### 4. УМНОЕ АВТОУДАЛЕНИЕ ИНФОРМАЦИОННЫХ КОМАНД

**Концепция:**
- Информационные команды удаляются через 60 секунд
- Сообщение пользователя НЕ удаляется (оставляем контекст)
- Игровые события НЕ удаляются (ферма, казино, дуэли)

**Файл:** `app/features/balance/handlers.py`

**Изменение:**
```python
# БЫЛО:
# Удаляем команду пользователя через 5 сек
await deletion.schedule(session, message.chat.id, message.message_id, 5)

# Отправляем баланс и удаляем через 2 минуты
sent = await message.answer(...)
await deletion.schedule(session, sent.chat.id, sent.message_id, 120)

# СТАЛО:
# НЕ удаляем команду пользователя (оставляем контекст)

# Отправляем баланс и удаляем через 60 секунд
sent = await message.answer(...)
await deletion.schedule(session, sent.chat.id, sent.message_id, 60)
```

**Файл:** `app/features/profile/handlers.py`

**Изменение:**
```python
# ДОБАВИТЬ в конец функции profile_command:
deletion = get_deletion_service()
# Удаляем профиль через 60 секунд
await deletion.schedule(session, message.chat.id, sent.message_id, 60)
```

**Где применить:**
- ✅ `/profile` - удалять через 60 сек
- ✅ `/balance` - удалять через 60 сек
- ✅ `/achievements` - удалять через 60 сек (уже есть 300 сек, изменить на 60)
- ✅ `/top` - удалять через 60 сек (уже есть 300 сек, изменить на 60)
- ✅ `/weekly` - удалять через 60 сек (уже есть 300 сек, изменить на 60)
- ✅ `/families` - удалять через 60 сек (уже есть 300 сек, изменить на 60)
- ✅ `/marriage` - добавить удаление через 60 сек

**Где НЕ удалять:**
- ❌ `/farm` - игровое событие
- ❌ `/casino` - игровое событие
- ❌ `/duel` - игровое событие
- ❌ `/marry` - игровое событие
- ❌ `/treasure` - игровое событие

---

### 5. МИНИМАЛЬНАЯ НАВИГАЦИЯ

**Файл:** `app/core/keyboards.py`  
**Строка:** ~87 (в конец файла)

**Добавить:**
```python
def profile_navigation() -> InlineKeyboardMarkup:
    """Минимальная навигация для профиля."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Баланс", callback_data="quick:balance")
    builder.button(text="🏅 Ачивки", callback_data="quick:achievements")
    builder.adjust(2)
    return builder.as_markup()

def balance_navigation() -> InlineKeyboardMarkup:
    """Минимальная навигация для баланса."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 Профиль", callback_data="quick:profile")]
        ]
    )
```

**Файл:** `app/features/profile/handlers.py`  
**Строка:** ~90-97

**Изменение:**
```python
# БЫЛО:
from app.core.keyboards import quick_actions  # если используется

# Кнопка на сайт
profile_url = f"{settings.website_url}/profile/{user.user_id}"
keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Открыть профиль на сайте", url=profile_url)]
    ]
)

await message.answer(text, reply_markup=keyboard)

# СТАЛО:
from app.core.keyboards import profile_navigation

# Кнопка на сайт + навигация
profile_url = f"{settings.website_url}/profile/{user.user_id}"
builder = InlineKeyboardBuilder()
builder.row(InlineKeyboardButton(text="🌐 Открыть профиль на сайте", url=profile_url))
builder.row(
    InlineKeyboardButton(text="💰 Баланс", callback_data="quick:balance"),
    InlineKeyboardButton(text="🏅 Ачивки", callback_data="quick:achievements")
)

sent = await message.answer(text, reply_markup=builder.as_markup())
```

**Файл:** `app/features/balance/handlers.py`  
**Строка:** ~43

**Изменение:**
```python
# БЫЛО:
sent = await message.answer(balance_text)

# СТАЛО:
from app.core.keyboards import balance_navigation
sent = await message.answer(balance_text, reply_markup=balance_navigation())
```

**Результат:**

Профиль:
```
[🌐 Открыть профиль на сайте]
[💰 Баланс] [🏅 Ачивки]
```

Баланс:
```
[👤 Профиль]
```

---

## 📊 ИТОГОВАЯ ТАБЛИЦА ИЗМЕНЕНИЙ

| Файл | Что меняется | Строк |
|------|--------------|-------|
| `app/features/profile/handlers.py` | Имя в заголовок + навигация + автоудаление | ~5 |
| `app/features/balance/handlers.py` | Место в топе + навигация + автоудаление | ~8 |
| `app/features/achievements/handlers.py` | Время автоудаления (300→60) | ~1 |
| `app/features/ratings/handlers.py` | Время автоудаления (300→60) ×3 + упростить время браков | ~4 |
| `app/features/marriage/handlers.py` | Упростить время + добавить автоудаление | ~3 |
| `app/core/utils.py` | Новая функция format_marriage_duration_days | ~10 |
| `app/core/keyboards.py` | Функции навигации | ~15 |

**Всего:**
- Файлов: 7
- Добавлено строк: ~40
- Изменено строк: ~6

---

## 📝 ПРИМЕРЫ ИТОГОВЫХ СООБЩЕНИЙ

### ПРОФИЛЬ
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
*(Удаляется через 60 секунд)*

### БАЛАНС
```
💰 Андрей: 1,234 ешки · 💊 Аптекарь
🏆 Место в топе: #17

[👤 Профиль]
```
*(Удаляется через 60 секунд)*

### БРАК
```
💍 Андрей ❤️ Мария · вместе 12 дн.
```
*(Удаляется через 60 секунд)*

### ТОП СЕМЕЙ
```
💞 Крепкие семьи

🥇 Игрок1 ❤️ Игрок2 — 45 дн.
🥈 Игрок3 ❤️ Игрок4 — 38 дн.
🥉 Игрок5 ❤️ Игрок6 — 32 дн.
```
*(Удаляется через 60 секунд)*

---

## ✅ ЧТО НЕ МЕНЯЕТСЯ

- ❌ Казино
- ❌ Ферма
- ❌ Дуэли
- ❌ Достижения (логика)
- ❌ Экономика
- ❌ База данных
- ❌ Структура команд
- ❌ Тексты (кроме заголовка профиля)

---

## 🚀 ПЛАН РЕАЛИЗАЦИИ

### Шаг 1: Утилиты
- [ ] Добавить `format_marriage_duration_days` в `app/core/utils.py`

### Шаг 2: Клавиатуры
- [ ] Добавить `profile_navigation` в `app/core/keyboards.py`
- [ ] Добавить `balance_navigation` в `app/core/keyboards.py`

### Шаг 3: Профиль
- [ ] Изменить заголовок
- [ ] Добавить навигацию
- [ ] Добавить автоудаление (60 сек)

### Шаг 4: Баланс
- [ ] Добавить место в топе
- [ ] Добавить навигацию
- [ ] Изменить автоудаление (60 сек)

### Шаг 5: Браки
- [ ] Использовать `format_marriage_duration_days` в `/marriage`
- [ ] Использовать `format_marriage_duration_days` в `/families`
- [ ] Добавить автоудаление в `/marriage` (60 сек)

### Шаг 6: Рейтинги
- [ ] Изменить время автоудаления `/achievements` (300→60)
- [ ] Изменить время автоудаления `/top` (300→60)
- [ ] Изменить время автоудаления `/weekly` (300→60)
- [ ] Изменить время автоудаления `/families` (300→60)

### Шаг 7: Проверка
- [ ] Показать git diff --stat
- [ ] Показать примеры сообщений
- [ ] Получить подтверждение перед commit

---

**Конец документа**

**Статус:** 📋 ГОТОВ К РЕАЛИЗАЦИИ
