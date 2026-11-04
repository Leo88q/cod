#!/usr/bin/env bash
# Локальный запуск: API + Mini App на :8000, бот отдельно (нужен BOT_TOKEN в .env)
set -e
cd "$(dirname "$0")/.."
PUBLIC_URL="${PUBLIC_URL:-http://localhost:8000}" .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
