#!/usr/bin/env bash
# Бэкап БД «Сцены» (WAL-safe): sqlite3 .backup + сжатие. Запускать по крону раз в час.
# Использование: bash scripts/backup.sh [куда]   (по умолчанию backups/)
set -euo pipefail
cd "$(dirname "$0")/.."
DB="${DB_PATH:-scena.db}"
OUT="${1:-backups}"
mkdir -p "$OUT"
TS=$(date +%Y%m%d_%H%M%S)
DEST="$OUT/scena_$TS.db"
if command -v sqlite3 > /dev/null; then
  sqlite3 "$DB" ".backup '$DEST'"
else
  PYTHON="${PYTHON:-python3}"
  if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; fi
  "$PYTHON" - "$DB" "$DEST" << 'EOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
EOF
fi
gzip -f "$DEST"
# храним 30 последних
ls -t "$OUT"/scena_*.db.gz 2>/dev/null | tail -n +31 | xargs -r rm -f
echo "backup: $DEST.gz"
