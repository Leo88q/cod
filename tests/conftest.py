import os
import tempfile

# Env ДО импорта приложения: изолированная временная БД, анонимный dev-режим
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="scena-test-"), "test.db")
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["ALLOW_ANON"] = "1"
os.environ["SCHEDULER_INTERVAL"] = "3600"  # в тестах шедулер не должен лезть в БД параллельно
os.environ["RATE_LIMIT_PER_MIN"] = "100000"  # в тестах не душим rate limit
os.environ["RATE_LIMIT_GIFTS_PER_MIN"] = "100000"
os.environ["RATE_LIMIT_SUGGEST_PER_MIN"] = "100000"
os.environ.pop("BOT_TOKEN", None)

os.environ["RATE_LIMIT_UPLOAD_PER_MIN"] = "100000"
