#!/usr/bin/env bash
# fix-bot-port.sh — публикует внутренний API бота (порт 8081) наружу, чтобы
# сайт на Vercel мог достучаться до бота для выдачи подарков и кнопки
# "Повторить". Запускать НА VPS из папки с docker-compose.yml бота.
#
#   bash fix-bot-port.sh
#
# Идемпотентно: повторный запуск ничего не ломает.
set -euo pipefail

PORT="${INTERNAL_API_PORT:-8081}"
COMPOSE_FILE="docker-compose.yml"

echo "== VOZNYA: открыть порт внутреннего API бота ($PORT) =="

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "ОШИБКА: $COMPOSE_FILE не найден. Запусти скрипт из папки бота (где лежит docker-compose.yml)." >&2
  exit 1
fi

# выбрать docker compose v2 или v1
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "ОШИБКА: docker compose не найден." >&2
  exit 1
fi
echo "compose: $DC"

# 1) бэкап compose-файла
cp "$COMPOSE_FILE" "${COMPOSE_FILE}.bak.$(date +%Y%m%d%H%M%S)"
echo "бэкап compose-файла создан"

# 2) убедиться, что в .env заданы нужные переменные
touch .env
ensure_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    echo "  .env: $key уже задан — не трогаю"
  else
    echo "${key}=${val}" >> .env
    echo "  .env: добавил ${key}=${val}"
  fi
}
echo "проверяю .env ..."
ensure_env INTERNAL_API_ENABLED true
ensure_env INTERNAL_API_PORT "$PORT"
ensure_env INTERNAL_API_HOST 0.0.0.0
if ! grep -q "^INTERNAL_API_SECRET=" .env; then
  echo "  ВНИМАНИЕ: INTERNAL_API_SECRET в .env НЕ задан. Впиши тот же секрет, что BOT_INTERNAL_SECRET на Vercel." >&2
fi
if ! grep -q "^GIFTS_DELIVERY_ENABLED=" .env; then
  ensure_env GIFTS_DELIVERY_ENABLED true
fi

# 3) добавить проброс порта в сервис bot, если его ещё нет
if grep -qE "^\s*-\s*\"?${PORT}:${PORT}\"?" "$COMPOSE_FILE"; then
  echo "проброс порта ${PORT} уже есть в $COMPOSE_FILE"
else
  echo "добавляю проброс порта ${PORT} в сервис bot ..."
  python3 - "$COMPOSE_FILE" "$PORT" <<'PYEOF'
import re, sys
path, port = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    text = f.read()
lines = text.splitlines()
out = []
i = 0
inserted = False
n = len(lines)
while i < n:
    line = lines[i]
    out.append(line)
    if not inserted and re.match(r"^\s{2}bot:\s*$", line):
        # внутри сервиса bot: ищем отступ его свойств (обычно 4 пробела)
        j = i + 1
        prop_indent = "    "
        while j < n:
            m = re.match(r"^(\s+)\S", lines[j])
            if re.match(r"^\s{2}\S", lines[j]) and not lines[j].startswith("    "):
                break  # начался следующий сервис
            if m:
                prop_indent = m.group(1)
                break
            j += 1
        out.append(f"{prop_indent}ports:")
        out.append(f'{prop_indent}  - "{port}:{port}"')
        inserted = True
    i += 1
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
print("  ports добавлен" if inserted else "  не нашёл сервис bot: — добавь ports вручную")
PYEOF
fi

# 4) пересоздать контейнер бота
echo "пересоздаю контейнер bot ..."
$DC up -d --force-recreate bot

# 5) показать статус и проверить доступность
sleep 3
echo "== docker compose ps =="
$DC ps
echo "== проверка /internal/health (локально на VPS) =="
if curl -fsS "http://127.0.0.1:${PORT}/internal/health"; then
  echo ""
  echo "OK: внутренний API отвечает на 127.0.0.1:${PORT}"
else
  echo "" ; echo "НЕ отвечает локально — смотри логи: $DC logs --tail=40 bot" >&2
fi

echo ""
echo "== ДАЛЬШЕ ВРУЧНУЮ =="
echo "1) Открой порт в фаерволе:   sudo ufw allow ${PORT}/tcp"
echo "2) Узнай публичный IP:        curl ifconfig.me"
echo "3) Проверь снаружи (НЕ с VPS): curl http://<IP>:${PORT}/internal/health"
echo "4) На Vercel задай:           BOT_INTERNAL_URL=http://<IP>:${PORT}  и сделай Redeploy"
