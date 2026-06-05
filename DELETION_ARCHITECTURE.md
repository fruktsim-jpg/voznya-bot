# 🗑️ АРХИТЕКТУРА СИСТЕМЫ УДАЛЕНИЯ ИНФОРМАЦИОННЫХ СООБЩЕНИЙ

**Дата:** 5 июня 2026, 02:42 UTC+2  
**Статус:** 📋 ПРЕДЛОЖЕНИЕ АРХИТЕКТУРЫ

---

## 🎯 ЦЕЛЬ

Реализовать систему "одно активное информационное сообщение на пользователя" без десятков профилей в чате.

---

## 📊 ТЕКУЩАЯ СИСТЕМА

### Существующая архитектура:

**Файл:** `app/services/deletion.py`  
**Модель:** `ScheduledDeletion`

```python
class DeletionService:
    async def schedule(self, session, chat_id, message_id, delay_seconds):
        """Планирует удаление через N секунд"""
        delete_at = now_utc() + timedelta(seconds=delay_seconds)
        record = ScheduledDeletion(
            chat_id=chat_id,
            message_id=message_id,
            delete_at=delete_at
        )
        session.add(record)
        # Добавляет задачу в APScheduler
```

**Особенности:**
- ✅ Переживает рестарт бота
- ✅ Использует APScheduler
- ✅ Сохраняет в БД
- ❌ Нет связи с пользователем
- ❌ Нет типа сообщения
- ❌ Нет отмены предыдущих

---

## 🆕 ПРЕДЛАГАЕМАЯ АРХИТЕКТУРА

### Вариант 1: ГИБРИДНЫЙ (РЕКОМЕНДУЕМЫЙ)

**Концепция:**
- Информационные сообщения удаляются через 3-5 минут (таймер)
- При новом информационном запросе старое удаляется немедленно
- Игровые события не трогаем

**Преимущества:**
- ✅ Простая реализация
- ✅ Не требует изменений БД
- ✅ Работает с существующей системой
- ✅ Не теряется информация из-за короткого таймера

**Реализация:**

#### Шаг 1: Добавить метод в DeletionService

```python
# app/services/deletion.py

class DeletionService:
    # ... существующие методы ...
    
    async def schedule_info_message(
        self,
        session: AsyncSession,
        user_id: int,
        chat_id: int,
        message_id: int,
        delay_seconds: float = 180,  # 3 минуты по умолчанию
    ) -> None:
        """Планирует удаление информационного сообщения.
        
        Автоматически отменяет предыдущее информационное сообщение
        этого пользователя в этом чате.
        """
        # Отменяем предыдущее информационное сообщение пользователя
        await self.cancel_user_info_messages(session, user_id, chat_id)
        
        # Планируем новое удаление
        await self.schedule(session, chat_id, message_id, delay_seconds)
    
    async def cancel_user_info_messages(
        self,
        session: AsyncSession,
        user_id: int,
        chat_id: int,
    ) -> None:
        """Отменяет и удаляет предыдущие информационные сообщения пользователя."""
        # Получаем ID предыдущего сообщения из кэша/БД
        prev_message_id = await self._get_last_info_message(session, user_id, chat_id)
        
        if prev_message_id:
            # Удаляем немедленно
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=prev_message_id)
            except Exception:
                pass
            
            # Отменяем запланированное удаление
            await self._cancel_scheduled(session, chat_id, prev_message_id)
    
    async def _get_last_info_message(
        self,
        session: AsyncSession,
        user_id: int,
        chat_id: int,
    ) -> int | None:
        """Получает ID последнего информационного сообщения пользователя."""
        # Используем простой кэш в памяти или таблицу БД
        # Ключ: (user_id, chat_id) -> message_id
        pass
    
    async def _cancel_scheduled(
        self,
        session: AsyncSession,
        chat_id: int,
        message_id: int,
    ) -> None:
        """Отменяет запланированное удаление."""
        # Помечаем как done в БД
        # Удаляем задачу из APScheduler
        pass
```

#### Шаг 2: Добавить кэш последних сообщений

**Вариант 2.1: В памяти (простой)**

```python
# app/services/deletion.py

class DeletionService:
    def __init__(self, bot, sessionmaker, scheduler):
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.scheduler = scheduler
        # Кэш: {(user_id, chat_id): message_id}
        self._last_info_messages: dict[tuple[int, int], int] = {}
    
    async def schedule_info_message(self, session, user_id, chat_id, message_id, delay_seconds=180):
        # Удаляем предыдущее
        prev_id = self._last_info_messages.get((user_id, chat_id))
        if prev_id:
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=prev_id)
            except Exception:
                pass
        
        # Сохраняем новое
        self._last_info_messages[(user_id, chat_id)] = message_id
        
        # Планируем удаление
        await self.schedule(session, chat_id, message_id, delay_seconds)
```

**Вариант 2.2: В БД (надёжный)**

Добавить таблицу `user_last_info_message`:

```python
# app/models/user_last_info_message.py

class UserLastInfoMessage(Base):
    """Последнее информационное сообщение пользователя в чате."""
    __tablename__ = "user_last_info_messages"
    
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
```

#### Шаг 3: Использование в командах

```python
# app/features/profile/handlers.py

@router.message(RuCommand("профиль", "profile"))
async def profile_command(message: Message, session: AsyncSession, command_args: str) -> None:
    user_tg = message.from_user
    if user_tg is None:
        return
    
    user = await get_user(session, user_tg.id)
    if user is None:
        await message.answer("❌ Пользователь не найден в базе данных.")
        return
    
    text = await render_profile(session, user)
    sent = await message.answer(text, reply_markup=keyboard)
    
    # Используем новый метод
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=user_tg.id,
        chat_id=message.chat.id,
        message_id=sent.message_id,
        delay_seconds=180  # 3 минуты
    )
```

---

### Вариант 2: ПОЛНАЯ СИСТЕМА (СЛОЖНЕЕ)

**Концепция:**
- Храним тип сообщения (info/game)
- Храним владельца сообщения
- Автоматически отменяем предыдущие информационные

**Требует:**
- ✅ Изменение модели `ScheduledDeletion`
- ✅ Миграция БД
- ✅ Более сложная логика

**Изменения в БД:**

```python
# app/models/scheduled_deletion.py

class ScheduledDeletion(Base):
    __tablename__ = "scheduled_deletions"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delete_at: Mapped[datetime] = mapped_column(nullable=False)
    done: Mapped[bool] = mapped_column(default=False)
    
    # НОВЫЕ ПОЛЯ:
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # message_type: "info" | "game" | None
```

**Миграция:**

```bash
alembic revision --autogenerate -m "add user_id and message_type to scheduled_deletions"
alembic upgrade head
```

---

## 📊 СРАВНЕНИЕ ВАРИАНТОВ

| Критерий | Вариант 1: Гибрид (кэш) | Вариант 2: Полная система |
|----------|-------------------------|---------------------------|
| Сложность | ⭐⭐ Средняя | ⭐⭐⭐⭐ Высокая |
| Изменения БД | ❌ Не требуется | ✅ Требуется миграция |
| Надёжность | ⭐⭐⭐ Хорошая | ⭐⭐⭐⭐ Отличная |
| Переживает рестарт | ⚠️ Частично (кэш теряется) | ✅ Полностью |
| Время реализации | ~30 минут | ~2 часа |

---

## ✅ РЕКОМЕНДАЦИЯ

### Использовать Вариант 1 (Гибрид с кэшем в памяти)

**Почему:**
1. Простая реализация
2. Не требует миграции БД
3. Решает основную проблему
4. Легко тестировать

**Компромисс:**
- После рестарта бота кэш теряется
- Но это не критично: старые сообщения всё равно удалятся по таймеру

---

## 🔧 ПЛАН РЕАЛИЗАЦИИ (Вариант 1)

### Шаг 1: Расширить DeletionService

**Файл:** `app/services/deletion.py`

```python
class DeletionService:
    def __init__(self, bot, sessionmaker, scheduler):
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.scheduler = scheduler
        # Кэш последних информационных сообщений
        self._last_info_messages: dict[tuple[int, int], int] = {}
    
    async def schedule_info_message(
        self,
        session: AsyncSession,
        user_id: int,
        chat_id: int,
        message_id: int,
        delay_seconds: float = 180,
    ) -> None:
        """Планирует удаление информационного сообщения.
        
        Автоматически удаляет предыдущее информационное сообщение
        этого пользователя в этом чате.
        """
        # Удаляем предыдущее сообщение пользователя
        key = (user_id, chat_id)
        prev_message_id = self._last_info_messages.get(key)
        
        if prev_message_id and prev_message_id != message_id:
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=prev_message_id)
            except Exception:
                # Сообщение уже удалено или недоступно
                pass
        
        # Сохраняем новое сообщение
        self._last_info_messages[key] = message_id
        
        # Планируем удаление через delay_seconds
        await self.schedule(session, chat_id, message_id, delay_seconds)
```

**Добавлено:**
- ~20 строк кода
- Кэш в памяти
- Метод `schedule_info_message`

### Шаг 2: Использовать в командах

**Информационные команды:**
- `/profile`
- `/balance`
- `/achievements`
- `/top`
- `/weekly`
- `/families`
- `/marriage`

**Пример использования:**

```python
# БЫЛО:
deletion = get_deletion_service()
await deletion.schedule(session, sent.chat.id, sent.message_id, 300)

# СТАЛО:
deletion = get_deletion_service()
await deletion.schedule_info_message(
    session,
    user_id=message.from_user.id,
    chat_id=message.chat.id,
    message_id=sent.message_id,
    delay_seconds=180  # 3 минуты
)
```

---

## 📝 ПРИМЕРЫ РАБОТЫ

### Сценарий 1: Последовательные команды

```
[10:00:00] Андрей: /профиль
[10:00:01] Бот: 👤 Профиль — Андрей ... (message_id=100)
           Запланировано удаление через 3 мин

[10:00:15] Андрей: /баланс
[10:00:16] Бот удаляет message_id=100 (профиль)
[10:00:16] Бот: 💰 Андрей: 1,234 ешки ... (message_id=101)
           Запланировано удаление через 3 мин

[10:00:30] Андрей: /топ
[10:00:31] Бот удаляет message_id=101 (баланс)
[10:00:31] Бот: 🏆 Богачи Возни ... (message_id=102)
           Запланировано удаление через 3 мин

[10:03:31] Бот удаляет message_id=102 (топ) по таймеру
```

**Результат:** В чате остаётся только последнее информационное сообщение

### Сценарий 2: Игровые события не трогаем

```
[10:00:00] Андрей: /профиль
[10:00:01] Бот: 👤 Профиль — Андрей ... (message_id=100)

[10:00:15] Андрей: /ферма
[10:00:16] Бот: 💊 Андрей поднял 50 ешек ... (message_id=101)
           НЕ удаляет профиль (разные типы)

[10:00:30] Андрей: /казино 20
[10:00:31] Бот: 🎰 Казино в шоке +40 ... (message_id=102)
           НЕ удаляет ферму (игровые события)

[10:03:01] Бот удаляет message_id=100 (профиль) по таймеру
```

**Результат:** Игровые события остаются, информационные удаляются

---

## 🚀 ИТОГОВЫЙ ПЛАН

### Изменения:

1. **`app/services/deletion.py`**
   - Добавить кэш `_last_info_messages`
   - Добавить метод `schedule_info_message`
   - ~20 строк кода

2. **Информационные команды (7 файлов)**
   - Заменить `schedule()` на `schedule_info_message()`
   - ~1-2 строки на команду

### Статистика:

- Файлов: 8
- Добавлено строк: ~35
- Изменено строк: ~7
- Миграций БД: 0
- Время реализации: ~30 минут

---

## ❓ ВОПРОСЫ ДЛЯ УТВЕРЖДЕНИЯ

1. **Время удаления** - 3 минуты подходит?
2. **Кэш в памяти** - согласны с потерей после рестарта?
3. **Альтернатива** - нужна ли таблица в БД вместо кэша?

---

**Конец документа**

**Статус:** 📋 Ожидает утверждения
