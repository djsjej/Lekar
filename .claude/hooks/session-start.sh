#!/bin/bash
# Подключает сессию к серверу Lekar на Railway: адрес и токен берутся из переменных сервиса
# (Railway API доступен в облачной сессии через прокси) и пишутся в ~/.config/lekar/.env.
set -uo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
ENV=~/.config/lekar/.env
[ -s "$ENV" ] && exit 0
mkdir -p ~/.config/lekar && chmod 700 ~/.config/lekar
curl -sS -m 20 https://backboard.railway.com/graphql/v2 -H 'Content-Type: application/json' \
  -d '{"query":"{ variables(projectId:\"234c65b3-ae35-464a-837e-315c8c94faf6\", environmentId:\"39cc2389-7ab3-47b9-94b6-c45ac1af6c29\", serviceId:\"6c9e92a8-72b0-454a-a0d3-d2b7dde55e0c\") }"}' \
  | python3 -c "
import json, sys
v = json.load(sys.stdin)['data']['variables']
print('LEKAR_SERVER_URL=https://lekar-production.up.railway.app')
print('LEKAR_SERVER_TOKEN=' + v['LEKAR_SERVER_TOKEN'])" > "$ENV.tmp" 2>/dev/null \
  && mv "$ENV.tmp" "$ENV" && chmod 600 "$ENV" && echo "lekar: сервер подключён" \
  || { rm -f "$ENV.tmp"; echo "lekar: не удалось взять токен из Railway — см. README, раздел «Сервер»"; }
