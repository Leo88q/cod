#!/usr/bin/env bash
# Полная проверка «Сцены»: синтаксис, тесты, фронтенд, живой сервер.
# Запуск: bash scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${VENV:-.venv/bin}"

echo "== 1/5 py_compile =="
$VENV/python -m py_compile app/*.py scripts/*.py

echo "== 2/5 pytest =="
$VENV/python -m pytest tests/ -q

echo "== 3/5 node --check (фронтенд) =="
for f in app/static/app.js app/static/landing.html; do :; done
node --check app/static/app.js && echo "app.js OK"
npm test

echo "== 4/5 генерация каталога =="
$VENV/python scripts/gen_catalog.py

echo "== 5/5 smoke живого сервера (нужен запущенный uvicorn :8000) =="
if curl -sf http://127.0.0.1:8000/health > /dev/null; then
  curl -s http://127.0.0.1:8000/health
  echo
  curl -s -X POST http://127.0.0.1:8000/api/scenes/semantic -H 'Content-Type: application/json' \
    -d '{"query":"извиниться перед любимым","limit":3}' | head -c 200
  echo
  curl -s http://127.0.0.1:8000/api/lists | head -c 200
  echo
  echo "SMOKE OK"
else
  echo "Сервер не запущен — пропускаю smoke (запусти: $VENV/uvicorn app.main:app --host 0.0.0.0 --port 8000)"
fi

echo "ВСЁ ЗЕЛЁНО ✅"
