#!/usr/bin/env bash
# ============================================================================
#  Резервная копия базы данных бота "Возня".
#  Делает дамп PostgreSQL из docker-compose в папку backups/.
#
#  Запуск вручную:
#      bash scripts/backup.sh
#
#  Автоматически (раз в сутки в 4:00) через crontab -e:
#      0 4 * * * cd /путь/к/проекту && bash scripts/backup.sh >> backups/backup.log 2>&1
# ============================================================================
set -euo pipefail

# Переходим в корень проекта (на уровень выше папки scripts).
cd "$(dirname "$0")/.."

# Загружаем переменные из .env (POSTGRES_USER, POSTGRES_DB).
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

mkdir -p backups
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTFILE="backups/voznya_${STAMP}.sql.gz"

echo "Создаю бэкап → ${OUTFILE}"
docker compose exec -T db pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" | gzip > "${OUTFILE}"

# Храним только последние 30 копий.
ls -1t backups/voznya_*.sql.gz 2>/dev/null | tail -n +31 | xargs -r rm -f

echo "Готово. Текущие бэкапы:"
ls -lh backups/voznya_*.sql.gz
