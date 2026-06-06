# 🎨 ПЛАН ДОПОЛНИТЕЛЬНЫХ UX-УЛУЧШЕНИЙ

**Дата:** 5 июня 2026, 03:27 UTC+2  
**Статус:** 📋 ОЖИДАЕТ УТВЕРЖДЕНИЯ

---

## 📋 ОБЗОР ИЗМЕНЕНИЙ

### Файлы, которые будут изменены:

1. **`app/services/deletion.py`** - расширение системы удаления (хранение команд пользователя)
2. **`app/features/marriage/handlers.py`** - подтверждение развода
3. **`app/features/treasure/handlers.py`** - новые алиасы и тексты кладов
4. **`app/features/help/handlers.py`** - интеграция с системой автоудаления
5. **`app/features/achievements/handlers.py`** - убрать кнопку "Все достижения"
6. **`app/features/profile/handlers.py`** - убрать кнопку на сайт (опционально)
7. **`app/settings/texts.py`** - новый HELP, новые тексты кладов, тексты развода
8. **`app/core/keyboards.py`** - кнопка подтверждения развода

**Итого:** 8 файлов

---

## 1. 🗑️ УЛУЧШЕНИЕ АВТОУДАЛЕНИЯ

### Текущая проблема:

```
Пользователь: /профиль
Бот: [профиль]

Пользователь: /баланс
Бот: [удаляет профиль, показывает баланс]

Результат: команда "/профиль" висит в чате
```

### Новая логика:

Хранить пару `(user_command_id, bot_response_id)` и удалять обе при новом запросе.

### Реализация в `app/services/deletion.py`:

```python
class DeletionService:
    def __init__(self, bot, sessionmaker, scheduler):
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.scheduler = scheduler
        # БЫЛО: только bot message
        # self._last_info_messages: dict[tuple[int, int], int] = {}
        
        # СТАЛО: пара (user_command_id, bot_response_id)
        self._last_info_messages: dict[tuple[int, int], tuple[int, int]] = {}
    
    async def schedule_info_message(
        self,
        session: AsyncSession,
        user_id: int,
        chat_id: int,
        user_command_id: int,  # НОВЫЙ параметр
        bot_message_id: int,
        delay_seconds: float = 180,
    ) -> None:
        """Планирует удаление информационного сообщения.
        
        Автоматически удаляет предыдущую пару (команда + ответ)
        этого пользователя в этом чате.
        """
        key = (user_id, chat_id)
        prev_pair = self._last_info_messages.get(key)
        
        if prev_pair:
            prev_user_cmd, prev_bot_msg = prev_pair
            # Удаляем предыдущую команду пользователя
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=prev_user_cmd)
            except Exception:
                pass
            # Удаляем предыдущий ответ бота
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=prev_bot_msg)
            except Exception:
                pass
        
        # Сохраняем новую пару
        self._last_info_messages[key] = (user_command_id, bot_message_id)
        
        # Планируем удаление через delay_seconds
        await self.schedule(session, chat_id, bot_message_id, delay_seconds)
        await self.schedule(session, chat_id, user_command_id, delay_seconds)
```

### Использование в командах:

```python
# БЫЛО:
await deletion.schedule_info_message(
    session,
    user_id=user.id,
    chat_id=message.chat.id,
    message_id=sent.message_id,
    delay_seconds=180
)

# СТАЛО:
await deletion.schedule_info_message(
    session,
    user_id=user.id,
    chat_id=message.chat.id,
    user_command_id=message.message_id,  # команда пользователя
    bot_message_id=sent.message_id,       # ответ бота
    delay_seconds=180
)
```

---

## 2. 💔 ПОДТВЕРЖДЕНИЕ РАЗВОДА

### Текущая логика:

```python
@router.message(RuCommand("развод", "divorce", "развестись", "разрыв"))
async def cmd_divorce(...):
    # Разводим сразу без подтверждения
    marriage.divorced_at = now_utc()
```

### Новая логика:

```python
@router.message(RuCommand("развод", "divorce", "развестись", "разрыв", "расстаться"))
async def cmd_divorce(...):
    marriage = await service.get_marriage(session, user.id)
    if marriage is None:
        await message.answer(texts.DIVORCE_NO_MARRIAGE)
        return
    
    partner_id = (
        marriage.user_id_2 if marriage.user_id_1 == user.id else marriage.user_id_1
    )
    partner = await session.get(User, partner_id)
    partner_name = partner.display_name() if partner else "партнёр"
    
    # Показываем подтверждение
    await message.answer(
        texts.DIVORCE_CONFIRM.format(partner=partner_name),
        reply_markup=divorce_confirm(user.id, partner_id)
    )


@router.callback_query(F.data.startswith("divorce:confirm:"))
async def cb_divorce_confirm(callback: CallbackQuery, session: AsyncSession):
    """Подтверждение развода."""
    parts = callback.data.split(":")
    user_id = int(parts[2])
    partner_id = int(parts[3])
    
    # Защита: только инициатор
    if callback.from_user.id != user_id:
        await callback.answer(texts.CB_NOT_YOURS, show_alert=True)
        return
    
    marriage = await service.get_marriage(session, user_id)
    if marriage is None:
        await callback.answer("Брак уже расторгнут", show_alert=True)
        return
    
    # Разводим
    marriage.divorced_at = now_utc()
    
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            texts.DIVORCE_DONE.format(
                first=mention(user_id, callback.from_user.first_name, callback.from_user.username),
                second=await _mention_of(session, partner_id),
            )
        )
    await callback.answer()


@router.callback_query(F.data.startswith("divorce:cancel:"))
async def cb_divorce_cancel(callback: CallbackQuery, session: AsyncSession):
    """Отмена развода."""
    parts = callback.data.split(":")
    user_id = int(parts[2])
    
    if callback.from_user.id != user_id:
        await callback.answer(texts.CB_NOT_YOURS, show_alert=True)
        return
    
    if callback.message:
        await callback.message.edit_text("❌ Развод отменён")
    await callback.answer()
```

### Новая кнопка в `app/core/keyboards.py`:

```python
def divorce_confirm(user_id: int, partner_id: int) -> InlineKeyboardMarkup:
    """Кнопки подтверждения развода."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💔 Да, расстаться",
                    callback_data=f"divorce:confirm:{user_id}:{partner_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"divorce:cancel:{user_id}"
                )
            ]
        ]
    )
```

### Новые тексты в `app/settings/texts.py`:

```python
DIVORCE_CONFIRM = "⚠️ Вы уверены, что хотите расторгнуть брак с {partner}?"
DIVORCE_NO_MARRIAGE = "💔 {mention}, ты не в браке."
DIVORCE_DONE = "💔 Всё. {first} 💔 {second}"
```

---

## 3. 📦 УЛУЧШЕНИЕ КЛАДОВ

### Новые алиасы команды:

```python
# БЫЛО:
@router.message(RuCommand("снять", "claim"))

# СТАЛО:
@router.message(RuCommand("снять", "claim", "клад", "забрать", "открыть"))
```

### Новые тексты в `app/settings/texts.py`:

```python
TREASURE_CLAIM_VARIANTS = [
    "📦 {mention} раскопал клад: +{reward}",
    "📦 {mention} нашёл чемодан: +{reward}",
    "📦 Клад достался {mention}: +{reward}",
    "📦 {mention} забрал лут: +{reward}",
    "📦 {mention} успел первым: +{reward}",
    "📦 Чемодан вскрыл {mention}: +{reward}",
    "📦 {mention} поднял клад: +{reward}",
    "📦 Лут у {mention}: +{reward}",
]
```

**Обоснование:**
- Короткие (1 строка)
- Понятные
- Без кринжа
- Разнообразные

---

## 4. 🏅 УБРАТЬ КНОПКУ "ВСЕ ДОСТИЖЕНИЯ"

### Текущая логика:

```python
sent = await message.answer(
    await render_achievements_compact(session, user_id, first_name, username),
    reply_markup=achievements_full_button(user_id)  # ← УБРАТЬ
)
```

### Новая логика:

```python
sent = await message.answer(
    await render_achievements_compact(session, user_id, first_name, username)
    # Без кнопки
)
```

**Обоснование:**
- Кнопка показывает огромный список закрытых достижений
- Не несёт пользы
- Загромождает интерфейс

**Альтернатива (если нужно):**
- Показывать только ближайшие к открытию (требует изменения логики)

---

## 5. 🔘 КНОПКИ В ПРОФИЛЕ

### Текущие кнопки:

```python
keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Открыть профиль на сайте", url=profile_url)]
    ]
)
```

### Рекомендация:

**ОСТАВИТЬ** кнопку на сайт - она полезна для:
- Просмотра полной статистики
- Истории транзакций
- Графиков

**НЕ ДОБАВЛЯТЬ** лишние кнопки типа:
- "Баланс" (есть команда)
- "Достижения" (есть команда)
- "Топ" (есть команда)

---

## 6. 🔤 НОВЫЕ АЛИАСЫ КОМАНД

### Профиль:

```python
# БЫЛО:
@router.message(RuCommand("профиль", "profile"))

# СТАЛО:
@router.message(RuCommand("профиль", "profile", "проф"))
```

### Баланс:

```python
# УЖЕ ЕСТЬ:
@router.message(RuCommand("баланс", "balance", "бал", "деньги", "бабки"))
# ✅ Отлично!
```

### Достижения:

```python
# УЖЕ ЕСТЬ:
@router.message(RuCommand("ачивки", "achievements", "ачивы", "достижения"))
# ✅ Отлично!
```

### Топ:

```python
# УЖЕ ЕСТЬ:
@router.message(RuCommand("топ", "top", "рейтинг", "лидеры"))
# ✅ Отлично!
```

### Семьи:

```python
# УЖЕ ЕСТЬ:
@router.message(RuCommand("семьи", "families", "браки"))
# ✅ Отлично!
```

### Клад:

```python
# БУДЕТ ДОБАВЛЕНО (см. пункт 3):
@router.message(RuCommand("снять", "claim", "клад", "забрать", "открыть"))
```

### Развод:

```python
# БУДЕТ ДОБАВЛЕНО:
@router.message(RuCommand("развод", "divorce", "развестись", "разрыв", "расстаться"))
```

---

## 7. 📖 НОВЫЙ HELP

### Текущий HELP:

```python
HELP = (
    "🎮 <b>Возня</b> · команды (со слэшем и без)\n"
    "💊 ферма — поднять ешек (раз в 4 ч)\n"
    "💰 баланс · профиль · ачивки\n"
    "🎰 казино <ставка> — рискнуть\n"
    "⚔️ бой @ник — дуэль\n"
    "🏆 топ · топнеделя\n"
    "📦 снять — забрать клад\n"
    "🏳️‍🌈 пидор · 💞 пара — номинации дня\n"
    "💍 жениться · да · брак · развод · семьи\n"
    "💡 кнопки есть у фермы, казино и профиля"
)
```

### Новый HELP (по категориям):

```python
HELP = (
    "🎮 <b>Возня</b> — команды бота\n\n"
    
    "💰 <b>Экономика</b>\n"
    "• ферма — поднять ешек (раз в 4 ч)\n"
    "• баланс — твои ешки и место в топе\n"
    "• казино <ставка> — рискнуть\n"
    "• клад — забрать чемодан\n\n"
    
    "👤 <b>Игрок</b>\n"
    "• профиль — твоя карточка\n"
    "• ачивки — достижения\n"
    "• топ — богачи Возни\n"
    "• топнеделя — кто больше заработал\n\n"
    
    "💍 <b>Социальное</b>\n"
    "• жениться @ник — предложение\n"
    "• да — согласиться на брак\n"
    "• брак — инфо о браке\n"
    "• расстаться — развод\n"
    "• семьи — топ браков\n\n"
    
    "🎯 <b>Активности</b>\n"
    "• бой @ник — дуэль\n"
    "• пидор — номинация дня\n"
    "• пара — пара дня\n\n"
    
    "💡 Команды работают со слешем и без:\n"
    "<code>/профиль</code> = <code>профиль</code>"
)
```

**Изменения:**
- ✅ Разбито по категориям
- ✅ Читается за 5 секунд
- ✅ Понятная структура
- ✅ Не перегружено
- ✅ Показаны основные команды

### Интеграция HELP с автоудалением:

```python
# app/features/help/handlers.py

@router.message(RuCommand("помощь", "help", "старт", "start"))
async def cmd_help(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает список команд."""
    user = message.from_user
    if user is None:
        return
    
    sent = await message.answer(texts.HELP)
    
    # Интеграция с системой автоудаления
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=user.id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
        delay_seconds=180
    )
```

---

## 📊 СТАТИСТИКА ИЗМЕНЕНИЙ

### Файлы:

| Файл | Изменений | Описание |
|------|-----------|----------|
| `app/services/deletion.py` | ~20 строк | Хранение пары (команда, ответ) |
| `app/features/marriage/handlers.py` | ~60 строк | Подтверждение развода |
| `app/features/treasure/handlers.py` | ~5 строк | Новые алиасы |
| `app/features/help/handlers.py` | ~10 строк | Интеграция с автоудалением |
| `app/features/achievements/handlers.py` | -2 строки | Убрать кнопку |
| `app/features/profile/handlers.py` | ~5 строк | Обновить вызов deletion |
| `app/features/balance/handlers.py` | ~5 строк | Обновить вызов deletion |
| `app/features/ratings/handlers.py` | ~15 строк | Обновить вызовы deletion |
| `app/settings/texts.py` | ~30 строк | Новые тексты |
| `app/core/keyboards.py` | ~15 строк | Кнопка развода |

**Итого:**
- Файлов: 10
- Добавлено: ~165 строк
- Удалено: ~2 строки
- Миграций БД: 0

---

## ✅ ЧЕКЛИСТ РЕАЛИЗАЦИИ

- [ ] Расширить `DeletionService` для хранения пар сообщений
- [ ] Обновить все информационные команды (передавать user_command_id)
- [ ] Добавить подтверждение развода с кнопками
- [ ] Добавить новые алиасы для кладов
- [ ] Обновить тексты кладов
- [ ] Убрать кнопку "Все достижения"
- [ ] Обновить HELP
- [ ] Интегрировать HELP с автоудалением
- [ ] Добавить алиас "проф" для профиля
- [ ] Добавить алиас "расстаться" для развода
- [ ] Тестирование
- [ ] Commit и push

---

## ❓ ВОПРОСЫ ДЛЯ УТВЕРЖДЕНИЯ

1. **Автоудаление команд** - согласны с удалением команды пользователя вместе с ответом?
2. **Подтверждение развода** - нужна ли кнопка "Отмена" или только "Да, расстаться"?
3. **Тексты кладов** - устраивают предложенные варианты?
4. **HELP** - устраивает новая структура по категориям?
5. **Кнопка достижений** - просто убрать или заменить на "Ближайшие"?
6. **Кнопка в профиле** - оставить кнопку на сайт?

---

**Статус:** 📋 ОЖИДАЕТ УТВЕРЖДЕНИЯ

После утверждения начну реализацию.
