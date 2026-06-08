# 🔄 ИНСТРУКЦИЯ ПО ПЕРЕЗАПУСКУ БОТА НА VPS

## ⚠️ ПРОБЛЕМА
Бот на VPS работает на **старой версии кода**. Новые изменения не применены:
- `/help` не работает
- `/бой 50` (открытые вызовы) не работают
- Другие улучшения не активны

## ✅ РЕШЕНИЕ: Перезапустить бота на VPS

### Вариант 1: Docker (рекомендуется)

```bash
# 1. Подключиться к VPS по SSH
ssh user@your-vps-ip

# 2. Перейти в директорию проекта
cd /path/to/voznya-bot

# 3. Обновить код с GitHub
git pull origin main

# 4. Перезапустить контейнеры
docker-compose restart

# 5. Проверить логи
docker-compose logs -f --tail=50
```

### Вариант 2: Systemd Service

```bash
# 1. Подключиться к VPS
ssh user@your-vps-ip

# 2. Перейти в директорию
cd /path/to/voznya-bot

# 3. Обновить код
git pull origin main

# 4. Перезапустить сервис
sudo systemctl restart voznya-bot

# 5. Проверить статус
sudo systemctl status voznya-bot

# 6. Посмотреть логи
sudo journalctl -u voznya-bot -f
```

### Вариант 3: PM2

```bash
# 1. Подключиться к VPS
ssh user@your-vps-ip

# 2. Перейти в директорию
cd /path/to/voznya-bot

# 3. Обновить код
git pull origin main

# 4. Перезапустить через PM2
pm2 restart voznya-bot

# 5. Проверить логи
pm2 logs voznya-bot
```

### Вариант 4: Ручной запуск (если бот запущен вручную)

```bash
# 1. Подключиться к VPS
ssh user@your-vps-ip

# 2. Найти процесс бота
ps aux | grep python | grep voznya

# 3. Убить процесс (замените PID на реальный)
kill -9 <PID>

# 4. Перейти в директорию
cd /path/to/voznya-bot

# 5. Обновить код
git pull origin main

# 6. Запустить бота заново
python -m app.main
# или
nohup python -m app.main > bot.log 2>&1 &
```

## 🔍 Проверка после перезапуска

После перезапуска проверьте в Telegram:

1. ✅ `/help` - должна показать новое меню с категориями
2. ✅ `/бой 50` - должен создать открытый вызов
3. ✅ `/клад` - должен работать (новый алиас)
4. ✅ `/проф` - должен показать профиль (новый алиас)
5. ✅ `/расстаться` - должен показать подтверждение развода

## 📝 Что изменилось (последние коммиты)

```
0cc1686 - feat: add open duel challenges - /бой 50 for anyone to accept
7d9fe68 - fix: check target balance before duel challenge
3312c72 - fix: remove casino repeat button, update treasure/duel texts
1532be7 - fix: remove unused balance import from help handlers
b1dad81 - feat: UX improvements - single active info window, divorce confirmation, new aliases
```

## ❓ Если не помогло

1. Проверьте логи на ошибки:
   ```bash
   # Docker
   docker-compose logs -f
   
   # Systemd
   sudo journalctl -u voznya-bot -n 100
   
   # PM2
   pm2 logs voznya-bot --lines 100
   ```

2. Убедитесь, что код обновился:
   ```bash
   cd /path/to/voznya-bot
   git log --oneline -5
   # Должен быть коммит 0cc1686
   ```

3. Проверьте, что бот действительно перезапустился:
   ```bash
   # Docker
   docker-compose ps
   
   # Systemd
   sudo systemctl status voznya-bot
   
   # PM2
   pm2 list
   ```

## 🆘 Если ничего не работает

Напишите в чат вывод команд:
```bash
cd /path/to/voznya-bot
git log --oneline -3
docker-compose ps  # или pm2 list, или systemctl status voznya-bot
docker-compose logs --tail=50  # последние 50 строк логов
```
