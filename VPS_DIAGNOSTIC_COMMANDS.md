# 🔍 КОМАНДЫ ДЛЯ ДИАГНОСТИКИ VPS

**Цель:** Доказать, что контейнер на VPS использует коммит `ad7490e` и определить, почему команда `/профиль` не работает.

---

## ⚠️ ВАЖНО

Эти команды нужно выполнить **НА VPS**, а не локально.

Подключение к VPS:
```bash
ssh user@vps-host
```

---

## 1️⃣ ПРОВЕРКА DOCKER КОНТЕЙНЕРОВ

### Команда 1.1: Список запущенных контейнеров
```bash
docker ps
```

**Что искать:**
- Контейнер с именем `voznya-bot` или `voznya-bot-bot-1`
- Статус: `Up X hours/days`
- Порты (если есть)

**Пример вывода:**
```
CONTAINER ID   IMAGE              COMMAND           CREATED        STATUS        PORTS     NAMES
abc123def456   voznya-bot-bot     "python -m app.main"   2 days ago     Up 2 days               voznya-bot-bot-1
def789ghi012   postgres:16-alpine "docker-entrypoint..."  2 days ago     Up 2 days     5432/tcp  voznya-bot-db-1
```

### Команда 1.2: Docker Compose статус
```bash
docker compose ps
```

**Что искать:**
- Сервис `bot` - должен быть `running`
- Сервис `db` - должен быть `running`

**Пример вывода:**
```
NAME                COMMAND                  SERVICE   STATUS    PORTS
voznya-bot-bot-1    "python -m app.main"     bot       running
voznya-bot-db-1     "docker-entrypoint..."   db        running   5432/tcp
```

---

## 2️⃣ ПРОВЕРКА КОНТЕЙНЕРА БОТА

### Команда 2.1: Получить ID контейнера бота
```bash
CONTAINER_ID=$(docker ps --filter "name=bot" --format "{{.ID}}" | head -1)
echo "Container ID: $CONTAINER_ID"
```

### Команда 2.2: Inspect контейнера
```bash
docker inspect $CONTAINER_ID
```

**Что искать в выводе:**

#### 2.2.1 Рабочая директория
```json
"Config": {
    "WorkingDir": "/app"
}
```

#### 2.2.2 Команда запуска
```json
"Config": {
    "Cmd": ["python", "-m", "app.main"]
}
```

#### 2.2.3 Образ
```json
"Config": {
    "Image": "voznya-bot-bot"
}
```

#### 2.2.4 Время создания
```json
"Created": "2026-06-05T00:30:00.000000000Z"
```

**Если контейнер создан ДО коммита `2db509c` (00:26:22) → запущен старый код!**

#### 2.2.5 Монтированные тома
```json
"Mounts": [
    {
        "Type": "bind",
        "Source": "/path/to/voznya-bot",
        "Destination": "/app"
    }
]
```

**Важно:** Если есть bind mount → контейнер использует код из `Source`.

### Команда 2.3: Краткая информация
```bash
docker inspect $CONTAINER_ID --format='{{.Created}} | {{.Config.WorkingDir}} | {{.Config.Image}}'
```

---

## 3️⃣ ПРОВЕРКА РАБОЧЕЙ ДИРЕКТОРИИ КОНТЕЙНЕРА

### Команда 3.1: Войти в контейнер
```bash
docker exec -it $CONTAINER_ID /bin/sh
```

### Команда 3.2: Проверить рабочую директорию
```bash
pwd
ls -la
```

**Ожидается:**
```
/app
total 48
drwxr-xr-x    1 root     root          4096 Jun  5 00:30 .
drwxr-xr-x    1 root     root          4096 Jun  5 00:30 ..
-rw-r--r--    1 root     root          1234 Jun  5 00:26 .env
drwxr-xr-x    8 root     root          4096 Jun  5 00:26 app
-rw-r--r--    1 root     root           567 Jun  5 00:26 requirements.txt
```

### Команда 3.3: Проверить наличие .git
```bash
ls -la .git
```

**Если .git НЕ существует:**
- Контейнер использует скопированный код (из Dockerfile COPY)
- Нельзя проверить коммит внутри контейнера
- Нужно проверить коммит в исходной папке на хосте

**Если .git существует:**
- Контейнер использует bind mount
- Можно проверить коммит внутри контейнера

### Команда 3.4: Проверить файл handlers.py внутри контейнера
```bash
grep -A 5 "async def profile_command" app/features/profile/handlers.py
```

**Ожидается (с исправлением):**
```python
async def profile_command(message: Message, session: AsyncSession) -> None:
    """Показывает профиль игрока с кнопкой на сайт."""
    from app.repositories.users import get_user
```

**НЕ должно быть (старая версия):**
```python
async def profile_command(message: Message, user: User, session: AsyncSession) -> None:
```

### Команда 3.5: Выйти из контейнера
```bash
exit
```

---

## 4️⃣ ПРОВЕРКА ИСХОДНОЙ ПАПКИ НА ХОСТЕ

### Команда 4.1: Найти папку проекта
```bash
# Вариант 1: Проверить docker-compose.yml
docker compose config | grep -A 5 "build:"

# Вариант 2: Проверить возможные расположения
ls -la /home/*/voznya-bot 2>/dev/null
ls -la /opt/voznya-bot 2>/dev/null
ls -la /var/www/voznya-bot 2>/dev/null
ls -la ~/voznya-bot 2>/dev/null

# Вариант 3: Найти по .git
find /home /opt /var/www ~ -name ".git" -type d 2>/dev/null | grep voznya
```

### Команда 4.2: Перейти в папку проекта
```bash
cd /path/to/voznya-bot
pwd
```

### Команда 4.3: Проверить текущий коммит
```bash
git rev-parse HEAD
```

**Ожидается:**
```
ad7490ef919c62c20aae614fe09d10a71da633e8
```

**Если НЕ совпадает:**
- Код на VPS устарел
- Нужно выполнить `git pull`

### Команда 4.4: Проверить статус Git
```bash
git status
```

**Ожидается:**
```
On branch main
Your branch is up to date with 'origin/main'.

nothing to commit, working tree clean
```

**Если есть изменения:**
- Незакоммиченные изменения
- Контейнер может использовать изменённый код

### Команда 4.5: Проверить последние коммиты
```bash
git log --oneline -5
```

**Ожидается:**
```
ad7490e (HEAD -> main, origin/main) test
2db509c fix
6960b8e rework
d50c00e Fix: Add PostgreSQL port mapping
13b19fb release
```

### Команда 4.6: Проверить файл handlers.py на хосте
```bash
grep -A 5 "async def profile_command" app/features/profile/handlers.py
```

---

## 5️⃣ ПРОВЕРКА ЛОГОВ БОТА

### Команда 5.1: Последние 100 строк логов
```bash
docker compose logs bot --tail 100
```

**Что искать:**
- Сообщение о запуске: `Бот @voznyanlbot запущен`
- Ошибки при импорте модулей
- Ошибки при регистрации роутеров
- Ошибки при вызове команды `/профиль`

### Команда 5.2: Логи с фильтром по "profile"
```bash
docker compose logs bot | grep -i "profile"
```

**Что искать:**
- Ошибки импорта `profile_router`
- Ошибки в `profile_command`
- `TypeError` при вызове команды

### Команда 5.3: Логи с фильтром по ошибкам
```bash
docker compose logs bot | grep -i "error\|exception\|traceback"
```

### Команда 5.4: Логи в реальном времени
```bash
docker compose logs bot -f
```

**Затем в Telegram:**
- Отправить `/профиль`
- Наблюдать за логами

**Нажать Ctrl+C для выхода**

---

## 6️⃣ ПРОВЕРКА КОМАНДЫ /ПРОФИЛЬ

### Сценарий 1: Команда не зарегистрирована

**Проверка:**
```bash
docker exec $CONTAINER_ID python -c "
from app.features import get_feature_routers
routers = get_feature_routers()
print(f'Total routers: {len(routers)}')
for r in routers:
    print(f'- {r.name}')
"
```

**Ожидается:**
```
Total routers: 15
- welcome
- farm
- casino
- duel
- treasure
- pidor
- para
- marriage
- profile    ← ДОЛЖЕН БЫТЬ
- balance
- ratings
- achievements
- help
- admin
- quick
```

**Если `profile` отсутствует:**
- Роутер не зарегистрирован в `get_feature_routers()`
- Проверить `app/features/__init__.py`

### Сценарий 2: Команда не вызывается

**Проверка в логах:**
```bash
docker compose logs bot -f
```

**В Telegram отправить:**
```
/профиль
```

**Если в логах НЕТ никакой реакции:**
- Фильтр не срабатывает
- Бот не видит сообщение
- Бот не в том чате

**Если в логах ЕСТЬ ошибка:**
- Команда вызывается, но падает
- Смотреть traceback

### Сценарий 3: Фильтр не срабатывает

**Проверка:**
```bash
docker exec $CONTAINER_ID python -c "
from app.core.filters import RuCommand
from aiogram.types import Message

# Симуляция сообщения
class FakeMessage:
    text = '/профиль'

msg = FakeMessage()
filter = RuCommand('профиль')
# Проверка фильтра (упрощённо)
print('Filter test:', '/профиль' in msg.text)
"
```

### Сценарий 4: Запущен старый код

**Проверка:**
```bash
# Проверить дату создания контейнера
docker inspect $CONTAINER_ID --format='{{.Created}}'

# Проверить дату коммита 2db509c
git log 2db509c --format='%ai' -1
```

**Если контейнер создан ДО коммита:**
- Контейнер использует старый образ
- Нужно пересобрать: `docker compose up -d --build`

### Сценарий 5: Запущена другая копия бота

**Проверка:**
```bash
# Найти все контейнеры с Python
docker ps --filter "ancestor=python" --format "{{.ID}} {{.Names}} {{.Command}}"

# Найти все процессы Python на хосте
ps aux | grep "python.*app.main"

# Проверить все docker-compose проекты
docker compose ls
```

**Если найдено несколько:**
- Запущено несколько копий бота
- Определить, какая копия отвечает в Telegram

---

## 7️⃣ ДИАГНОСТИЧЕСКИЙ СКРИПТ (ПОЛНЫЙ)

Сохранить как `diagnose.sh` и выполнить на VPS:

```bash
#!/bin/bash

echo "=== VOZNYA BOT DIAGNOSTIC ==="
echo ""

echo "1. Docker containers:"
docker ps --filter "name=bot"
echo ""

echo "2. Container ID:"
CONTAINER_ID=$(docker ps --filter "name=bot" --format "{{.ID}}" | head -1)
echo "Container ID: $CONTAINER_ID"
echo ""

echo "3. Container created:"
docker inspect $CONTAINER_ID --format='{{.Created}}'
echo ""

echo "4. Container image:"
docker inspect $CONTAINER_ID --format='{{.Config.Image}}'
echo ""

echo "5. Working directory:"
docker inspect $CONTAINER_ID --format='{{.Config.WorkingDir}}'
echo ""

echo "6. Project directory:"
PROJECT_DIR=$(docker compose config | grep -A 5 "build:" | grep "context:" | awk '{print $2}')
echo "Project dir: $PROJECT_DIR"
cd "$PROJECT_DIR" 2>/dev/null || echo "ERROR: Cannot cd to project dir"
echo ""

echo "7. Current commit:"
git rev-parse HEAD 2>/dev/null || echo "ERROR: Not a git repository"
echo ""

echo "8. Git status:"
git status --short 2>/dev/null || echo "ERROR: Not a git repository"
echo ""

echo "9. Last 5 commits:"
git log --oneline -5 2>/dev/null || echo "ERROR: Not a git repository"
echo ""

echo "10. Check handlers.py in container:"
docker exec $CONTAINER_ID grep -A 2 "async def profile_command" app/features/profile/handlers.py 2>/dev/null || echo "ERROR: Cannot read file in container"
echo ""

echo "11. Check handlers.py on host:"
grep -A 2 "async def profile_command" app/features/profile/handlers.py 2>/dev/null || echo "ERROR: Cannot read file on host"
echo ""

echo "12. Recent bot logs (last 20 lines):"
docker compose logs bot --tail 20
echo ""

echo "13. Errors in logs:"
docker compose logs bot | grep -i "error\|exception" | tail -10
echo ""

echo "=== END OF DIAGNOSTIC ==="
```

**Запуск:**
```bash
chmod +x diagnose.sh
./diagnose.sh > diagnostic_output.txt
cat diagnostic_output.txt
```

---

## 8️⃣ ОЖИДАЕМЫЕ РЕЗУЛЬТАТЫ

### ✅ Если всё правильно:

1. **docker ps** - контейнер `voznya-bot-bot-1` запущен
2. **docker inspect** - создан после `2026-06-05 00:26:22`
3. **git rev-parse HEAD** - `ad7490ef919c62c20aae614fe09d10a71da633e8`
4. **handlers.py** - `async def profile_command(message: Message, session: AsyncSession)`
5. **Логи** - нет ошибок `TypeError`

### ❌ Возможные проблемы:

| Проблема | Признак | Решение |
|----------|---------|---------|
| Контейнер создан до исправления | Created < 00:26:22 | `docker compose up -d --build` |
| Код на хосте устарел | git rev-parse ≠ ad7490ef | `git pull && docker compose restart bot` |
| Старая версия handlers.py | `user: User` в сигнатуре | `git pull && docker compose up -d --build` |
| Запущено несколько ботов | Несколько контейнеров | Остановить лишние |
| Роутер не зарегистрирован | `profile` отсутствует в списке | Проверить `app/features/__init__.py` |

---

## 9️⃣ КОМАНДЫ ДЛЯ ИСПРАВЛЕНИЯ (ЕСЛИ НУЖНО)

**⚠️ ВНИМАНИЕ:** Эти команды ИЗМЕНЯЮТ состояние системы!

### Если код устарел:
```bash
cd /path/to/voznya-bot
git pull
docker compose up -d --build
```

### Если контейнер старый:
```bash
docker compose up -d --build
```

### Если просто перезапустить:
```bash
docker compose restart bot
```

### Если полностью пересоздать:
```bash
docker compose down
docker compose up -d --build
```

---

## 🔟 ОТЧЁТ

После выполнения команд заполнить:

```
=== VOZNYA BOT VPS DIAGNOSTIC REPORT ===

Date: _______________
VPS Host: _______________

1. Container ID: _______________
2. Container Created: _______________
3. Container Image: _______________
4. Project Directory: _______________
5. Current Commit: _______________
6. handlers.py signature: _______________
7. Errors in logs: _______________

Conclusion:
[ ] Container uses commit ad7490e
[ ] handlers.py has correct signature
[ ] No errors in logs
[ ] Command /профиль works

Issues found:
_______________________________________________
_______________________________________________
_______________________________________________
```

---

**Конец инструкций**
