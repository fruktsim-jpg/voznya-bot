# 🔍 ИНСТРУКЦИИ ПО ОТЛАДКЕ КОМАНДЫ /PROFILE

**Дата:** 5 июня 2026, 02:02 UTC+2  
**Статус:** Добавлено логирование для диагностики

---

## ✅ ЧТО СДЕЛАНО

### Добавлено логирование в обработчик

**Файл:** `app/features/profile/handlers.py`  
**Строки:** 70-75

```python
@router.message(RuCommand("профиль", "profile"))
async def profile_command(message: Message, session: AsyncSession) -> None:
    """Показывает профиль игрока с кнопкой на сайт."""
    print("=" * 80)
    print("PROFILE_HANDLER_REACHED")
    print(f"Message text: {message.text}")
    print(f"From user: {message.from_user.id if message.from_user else None}")
    print("=" * 80)
    
    from app.repositories.users import get_user
    # ... остальной код
```

---

## 📋 ШАГИ ДЛЯ ПРОВЕРКИ

### Шаг 1: Закоммитить изменения

```bash
git add app/features/profile/handlers.py
git commit -m "debug: добавлено логирование в profile_command"
git push origin main
```

### Шаг 2: Деплой на VPS

```bash
# На VPS
cd /path/to/voznya-bot
git pull
docker compose up -d --build
```

### Шаг 3: Открыть логи в реальном времени

```bash
docker compose logs bot -f
```

### Шаг 4: Отправить команду в Telegram

В чате отправить:
```
/profile
```

### Шаг 5: Наблюдать за логами

---

## 🔍 ВАРИАНТЫ РЕЗУЛЬТАТА

### Вариант A: Лог ПОЯВИЛСЯ

**Вывод в логах:**
```
================================================================================
PROFILE_HANDLER_REACHED
Message text: /profile
From user: 123456789
================================================================================
```

**Это значит:**
- ✅ Обработчик вызывается
- ✅ Фильтр `RuCommand` работает
- ✅ Роутер зарегистрирован
- ❌ Проблема ВНУТРИ функции

**Следующие действия:**
1. Добавить логи после каждой строки внутри функции
2. Найти точную строку, где выполнение ломается
3. Проверить traceback в логах

### Вариант B: Лог НЕ ПОЯВИЛСЯ

**Вывод в логах:**
```
(нет записей PROFILE_HANDLER_REACHED)
```

**Это значит:**
- ❌ Обработчик НЕ вызывается
- ❌ Фильтр не срабатывает ИЛИ
- ❌ Другой обработчик перехватывает сообщение ИЛИ
- ❌ Роутер не зарегистрирован правильно

**Следующие действия:**
1. Проверить порядок регистрации роутеров
2. Проверить, какие обработчики зарегистрированы
3. Проверить реализацию `RuCommand`
4. Добавить логи в `RuCommand.__call__`

---

## 🔍 ДОПОЛНИТЕЛЬНАЯ ДИАГНОСТИКА

### Если лог НЕ появился - проверить RuCommand

**Файл:** `app/core/filters.py`  
**Добавить логи в метод `__call__`:**

```python
async def __call__(self, message: Message) -> bool | dict:
    text = message.text or message.caption
    print(f"RuCommand: checking text='{text}', commands={self.commands}")
    
    if not text:
        print("RuCommand: no text, returning False")
        return False
    
    text = text.strip()
    first_token = text.split(maxsplit=1)[0]
    
    if first_token.startswith("/"):
        command = first_token[1:]
    elif self.allow_no_prefix:
        command = first_token
    else:
        print(f"RuCommand: no prefix and allow_no_prefix=False, returning False")
        return False
    
    if "@" in command:
        command = command.split("@", 1)[0]
    
    print(f"RuCommand: extracted command='{command}', checking against {self.commands}")
    
    if command.lower() not in self.commands:
        print(f"RuCommand: command not in list, returning False")
        return False
    
    parts = text.split(maxsplit=1)
    args = parts[1].strip() if len(parts) > 1 else ""
    print(f"RuCommand: MATCH! returning args='{args}'")
    return {"command_args": args}
```

### Проверить порядок роутеров

**Файл:** `app/features/__init__.py`

Убедиться, что `profile_router` в списке:

```python
return [
    welcome_router,
    farm_router,
    casino_router,
    duel_router,
    treasure_router,
    pidor_router,
    para_router,
    marriage_router,
    profile_router,  # ← ДОЛЖЕН БЫТЬ ЗДЕСЬ
    balance_router,
    ratings_router,
    achievements_router,
    help_router,
    admin_router,
    quick_router,
]
```

### Проверить регистрацию в main.py

**Файл:** `app/main.py`

```python
for router in get_feature_routers():
    print(f"Registering router: {router.name}")  # ← Добавить лог
    dp.include_router(router)
```

---

## 📊 ТАБЛИЦА ДИАГНОСТИКИ

| Проверка | Команда | Ожидаемый результат |
|----------|---------|---------------------|
| Лог в обработчике | `/profile` в Telegram | `PROFILE_HANDLER_REACHED` в логах |
| Лог в RuCommand | `/profile` в Telegram | `RuCommand: MATCH!` в логах |
| Регистрация роутера | Запуск бота | `Registering router: profile` в логах |
| Порядок роутеров | Проверка кода | `profile_router` в списке |

---

## 🎯 ФИНАЛЬНАЯ ПРОВЕРКА

### После добавления всех логов:

1. **Отправить `/profile`**
2. **Проверить логи:**
   - Есть ли `Registering router: profile`?
   - Есть ли `RuCommand: checking text='/profile'`?
   - Есть ли `RuCommand: MATCH!`?
   - Есть ли `PROFILE_HANDLER_REACHED`?

3. **Определить, где цепочка прерывается**

---

## 📝 ОТЧЁТ О РЕЗУЛЬТАТАХ

После тестирования заполнить:

```
=== РЕЗУЛЬТАТЫ ДИАГНОСТИКИ ===

Дата: _______________
Команда: /profile

Логи:
[ ] Registering router: profile
[ ] RuCommand: checking text='/profile'
[ ] RuCommand: MATCH!
[ ] PROFILE_HANDLER_REACHED

Вывод:
Цепочка прерывается на этапе: _______________

Причина: _______________
```

---

**Конец инструкций**
