#!/usr/bin/env bash
# Прогон и проверка Cases V1 + сид кейса + каталог Gifts.
# Запускать на машине с Docker (где живёт бот/БД). Из корня репозитория:
#   bash scripts/verify_cases_v1.sh
#
# Скрипт НЕ деструктивный: применяет миграции вперёд, гоняет тесты и печатает
# контрольные выборки. downgrade-проверку делает только если задан ALLOW_DOWNGRADE=1
# (на dev/stage), т.к. downgrade дропает таблицы кейсов.
set -euo pipefail

echo "== 1. Текущая ревизия миграций =="
docker compose exec -T bot alembic current

echo "== 2. Применяем миграции до head (0016 cases, 0017 seed, 0018 gifts) =="
docker compose exec -T bot alembic upgrade head
docker compose exec -T bot alembic current

echo "== 3. Импорт приложения (модели регистрируются без ошибок) =="
docker compose exec -T bot python -c "import app.main; import app.models; print('import OK')"

echo "== 4. Юнит-тесты выбора награды =="
docker compose exec -T bot pytest tests/test_cases_pick_reward.py -q

echo "== 5. Контроль данных: кейс «Кейс Бродяги» и его дроп-лист =="
docker compose exec -T db psql -U "${POSTGRES_USER:-voznya}" -d "${POSTGRES_DB:-voznya}" -c "
  SELECT d.item_code, d.name, d.open_cost_kind, d.consumes_key, d.is_active,
         i.type AS catalog_type
    FROM case_definitions d
    LEFT JOIN inventory_items i ON i.code = d.item_code
   WHERE d.item_code = 'case_vagabond';"

docker compose exec -T db psql -U "${POSTGRES_USER:-voznya}" -d "${POSTGRES_DB:-voznya}" -c "
  SELECT reward_kind, amount, weight,
         ROUND(100.0*weight/SUM(weight) OVER (), 2) AS pct, is_jackpot
    FROM case_rewards
   WHERE case_item_code = 'case_vagabond'
   ORDER BY weight DESC;"

echo "== 6. Контроль данных: таблица gift_catalog существует =="
docker compose exec -T db psql -U "${POSTGRES_USER:-voznya}" -d "${POSTGRES_DB:-voznya}" -c "
  SELECT to_regclass('public.gift_catalog') AS gift_catalog_table;"

echo "== 7. Висячие item-награды (должно быть 0 строк) =="
docker compose exec -T db psql -U "${POSTGRES_USER:-voznya}" -d "${POSTGRES_DB:-voznya}" -c "
  SELECT r.id, r.case_item_code, r.reward_item_code
    FROM case_rewards r
   WHERE r.reward_kind = 'item'
     AND NOT EXISTS (SELECT 1 FROM inventory_items i WHERE i.code = r.reward_item_code);"

if [ "${ALLOW_DOWNGRADE:-0}" = "1" ]; then
  echo "== 8. (dev) Проверка обратимости: downgrade -2 затем upgrade head =="
  docker compose exec -T bot alembic downgrade -2
  docker compose exec -T bot alembic upgrade head
  docker compose exec -T bot alembic current
fi

echo "== ГОТОВО. Если все шаги без ошибок — Cases V1 + сид + Gifts применены. =="
echo "Дальше — ручной цикл: выдать case_vagabond игроку (/admin players), затем"
echo "в боте /кейсы → /кейс case_vagabond → /открыть case_vagabond, и проверить"
echo "запись в case_openings + транзакцию ешек + витрину /cases на сайте."
