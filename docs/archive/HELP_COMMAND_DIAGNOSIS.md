# ДИАГНОСТИКА КОМАНДЫ /help

## Статус: ✅ КОД ПРАВИЛЬНЫЙ — ПРОБЛЕМА В ДЕПЛОЕ

---

## 1. Расположение обработчика

**Файл:** `app/features/help/handlers.py`

**Строки:** 16-33

```python
@router.message(RuCommand("помощь", "help", "старт", "start"))
async def cmd_help(message: Message, session: AsyncSession, command_args: str) -> None:
    """Показывает список команд."""
    user = message.from_user
    if user is None:
        return
    
    sent = await message.answer(texts.HELP)
    
    # Интеграция с системой "одно активное информационное окно"
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=user.id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
    )
```

---

## 2. Сигнатура функции

```python
async def cmd_help(message: Message, session: AsyncSession, command_args: str) -> None
```

**Проверка:** ✅ Правильная

- `message: Message` — получает сообщение от пользователя
- `session: AsyncSession` — получает сессию БД через middleware
- `command_args: str` — получает аргументы команды через RuCommand фильтр
- Возвращает `None` (стандартно для хендлеров)

**Сравнение с другими командами:**

```python
# profile/handlers.py
async def cmd_profile(message: Message, session: AsyncSession, command_args: str) -> None

# duel/handlers.py
async def cmd_duel(message: Message, session: AsyncSession, command_args: str) -> None

# farm/handlers.py
async def cmd_farm(message: Message, session: AsyncSession) -> None
```

**Вывод:** Сигнатура идентична работающим командам.

---

## 3. Фильтры

**Используется:** `RuCommand("помощь", "help", "старт", "start")`

**Файл фильтра:** `app/core/filters.py`

**Логика RuCommand:**

```python
class RuCommand(BaseFilter):
    def __init__(self, *commands: str, allow_no_prefix: bool = True) -> None:
        self.commands = {c.lower() for c in commands}
        self.allow_no_prefix = allow_no_prefix
    
    async def __call__(self, message: Message) -> bool | dict:
        text = message.text or message.caption
        if not text:
            return False
        text = text.strip()
        
        first_token = text.split(maxsplit=1)[0]
        if first_token.startswith("/"):
            command = first_token[1:]
        elif self.allow_no_prefix:
            command = first_token
        else:
            return False
        
        if "@" in command:
            command = command.split("@", 1)[0]
        if command.lower() not in self.commands:
            return False
        
        parts = text.split(maxsplit=1)
        args = parts[1].strip() if len(parts) > 1 else ""
        return {"command_args": args}
```

**Проверка:** ✅ Правильная

- Поддерживает команды со слэшем и без
- Поддерживает `@botusername`
- Передаёт `command_args` в хендлер
- Используется во всех других командах (которые работают)

---

## 4. Регистрация router

**Файл:** `app/features/help/handlers.py`, строка 13

```python
router = Router(name="help")
```

**Проверка:** ✅ Зарегистрирован

- Router создан с именем "help"
- Декоратор `@router.message()` привязывает хендлер к этому router

---

## 5. Подключение router в main

**Файл:** `app/features/__init__.py`

**Импорт (строка 25):**
```python
from app.features.help.handlers import router as help_router
```

**Включение в список (строка 48):**
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
    profile_router,
    balance_router,
    ratings_router,
    achievements_router,
    help_router,        # ← Строка 48
    admin_router,
    quick_router,
]
```

**Файл:** `app/main.py`, строки 77-78

```python
for router in get_feature_routers():
    dp.include_router(router)
```

**Проверка:** ✅ Подключён

- Router импортируется в `get_feature_routers()`
- Возвращается в списке роутеров
- Регистрируется в диспетчере через `dp.include_router()`

---

## 6. Все алиасы команды

**Определены в:** `app/features/help/handlers.py`, строка 16

```python
RuCommand("помощь", "help", "старт", "start")
```

**Поддерживаемые варианты:**

| Вариант | Со слэшем | Без слэша | С @botname |
|---------|-----------|-----------|------------|
| помощь | `/помощь` | `помощь` | `/помощь@bot` |
| help | `/help` | `help` | `/help@bot` |
| старт | `/старт` | `старт` | `/старт@bot` |
| start | `/start` | `start` | `/start@bot` |

**Всего:** 16 вариантов (4 алиаса × 4 формата)

**Проверка:** ✅ Алиасы правильные

- Русские и английские варианты
- Стандартные команды для Telegram-ботов
- Аналогично другим командам

---

## 7. Меню команд Telegram

**Файл:** `app/main.py`, строка 56

```python
BotCommand(command="help", description="❓ Помощь и список команд"),
```

**Проверка:** ✅ Добавлена в меню

- Команда отображается в меню бота
- Описание корректное
- Устанавливается при старте бота (строка 99)

---

## 8. Текст помощи

**Файл:** `app/settings/texts.py`

```python
HELP = (
    "🎮 <b>Возня</b> — команды бота\n\n"
    # ... остальной текст
)
```

**Проверка:** ✅ Текст определён

- Переменная `HELP` существует
- Используется в хендлере: `await message.answer(texts.HELP)`

---

## 9. Почему команда не вызывается

### ❌ Проблема НЕ в коде

**Доказательства:**

1. ✅ Обработчик существует и правильно оформлен
2. ✅ Фильтр RuCommand работает (другие команды работают)
3. ✅ Router зарегистрирован и подключён
4. ✅ Алиасы правильные
5. ✅ Сигнатура функции корректная
6. ✅ Middleware настроены (session передаётся)
7. ✅ Текст помощи определён

### ✅ Проблема в деплое на VPS

**Текущий commit в репозитории:**

```
79435ee (HEAD -> main, origin/main) fix: IntegrityError при создании дуэли без цели
```

**История последних изменений:**

```
79435ee - fix: IntegrityError при создании дуэли без цели
0cc1686 - feat: add open duel challenges
7d9fe68 - fix: check target balance before duel challenge
3312c72 - fix: remove casino repeat button, update treasure/duel texts
1532be7 - fix: remove unused balance import from help handlers  ← ВАЖНО!
b1dad81 - feat: UX improvements
```

**Commit `1532be7`:** "fix: remove unused balance import from help handlers"

Это означает, что в `help/handlers.py` недавно были изменения.

---

## 10. Диагностика VPS

### Проверить на VPS:

1. **Какой commit развёрнут:**
   ```bash
   cd /path/to/voznya-bot
   git log --oneline -1
   ```
   
   **Ожидается:** `79435ee` или новее

2. **Запущен ли бот:**
   ```bash
   docker ps
   # или
   systemctl status voznya-bot
   # или
   ps aux | grep python
   ```

3. **Логи бота:**
   ```bash
   docker logs voznya-bot --tail 100
   # или
   journalctl -u voznya-bot -n 100
   # или
   tail -f /var/log/voznya-bot.log
   ```

4. **Перезапущен ли бот после последнего pull:**
   ```bash
   # Проверить время последнего рестарта
   docker inspect voznya-bot | grep StartedAt
   # или
   systemctl status voznya-bot | grep Active
   ```

---

## 11. Решение

### Если на VPS старый код:

```bash
# На VPS
cd /path/to/voznya-bot
git pull origin main
docker-compose down
docker-compose up -d --build
```

### Если бот не перезапущен:

```bash
# На VPS
docker-compose restart
# или
systemctl restart voznya-bot
# или
docker restart voznya-bot
```

### Если логи показывают ошибку импорта:

Проверить, что все зависимости установлены:

```bash
docker-compose exec voznya-bot pip list
# Проверить наличие aiogram, sqlalchemy и т.д.
```

---

## 12. Конкретный commit hash для VPS

**Должен быть развёрнут:**

```
79435ee - fix: IntegrityError при создании дуэли без цели
```

**Или новее, если будут дополнительные коммиты.**

**Проверка на VPS:**

```bash
cd /path/to/voznya-bot
git rev-parse HEAD
```

**Ожидаемый вывод:** `79435ee...` (полный hash)

Если hash другой — нужен `git pull` и перезапуск.

---

## Итог

### ✅ Код правильный

- Обработчик существует
- Фильтры настроены
- Router подключён
- Алиасы корректные

### ❌ Проблема в деплое

**Наиболее вероятные причины:**

1. **На VPS старый код** (до commit `79435ee`)
2. **Бот не перезапущен** после последнего `git pull`
3. **Ошибка при старте бота** (проверить логи)

**Действия:**

1. Проверить commit hash на VPS
2. Сделать `git pull origin main`
3. Перезапустить бот
4. Проверить логи на ошибки

**Команда /help работает в коде** — нужно обновить деплой на VPS.
