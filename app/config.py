import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")                 # токен бота от @BotFather
BOT_USERNAME = os.getenv("BOT_USERNAME", "scena_bot") # без @, для deeplink-ов
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000")  # fallback; в осн. берём из запроса
WEBAPP_URL = os.getenv("WEBAPP_URL", "")               # если пусто — <base>/app
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "scena.db"))
MEDIA_DIR = os.getenv("MEDIA_DIR", os.path.join(BASE_DIR, "media"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")             # токен админки (X-Admin-Token)
SHOP_COMMISSION_PCT = float(os.getenv("SHOP_COMMISSION_PCT", "10"))
GIFT_TTL_DAYS = int(os.getenv("GIFT_TTL_DAYS", "30"))  # сколько живёт неоткрытый подарок
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "10"))  # час напоминаний о датах (UTC)
# В dev без BOT_TOKEN разрешаем анонимный доступ (для превью без Telegram).
# FAIL-SAFE: анонимный режим НЕ включается молча на prod-домене — только явно или на localhost.
_anon_env = os.getenv("ALLOW_ANON")
_is_local = any(h in (PUBLIC_URL or "") for h in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))
if _anon_env is not None:
    ALLOW_ANON = _anon_env == "1"
else:
    ALLOW_ANON = (not os.getenv("BOT_TOKEN")) and _is_local

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))  # лимит загрузки медиа (фото/голос)
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "@scena_support")  # поддержка (бот /help, лендинг)

# Webhook-режим бота (prod): WEBHOOK_URL=https://<домен> ; secret проверяется в заголовке
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# ЮKassa (prod). Если не заданы — dev-режим: оплата подтверждается кнопкой в Mini App
YUKASSA_SHOP_ID = os.getenv("YUKASSA_SHOP_ID", "")
YUKASSA_SECRET_KEY = os.getenv("YUKASSA_SECRET_KEY", "")
YUKASSA_API = "https://api.yookassa.ru/v3"

# Цены
PRICE_CARD_PACK_MIN = float(os.getenv("PRICE_CARD_PACK_MIN", "100"))     # мин. заказ пачки карточек, ₽
PRICE_PER_CARD = float(os.getenv("PRICE_PER_CARD", "50"))                # ₽ за карточку
PRICE_SUBSCRIPTION_MONTH = float(os.getenv("PRICE_SUBSCRIPTION_MONTH", "990"))
PRICE_PREMIUM_FILM = float(os.getenv("PRICE_PREMIUM_FILM", "499"))

SCHEDULER_INTERVAL = int(os.getenv("SCHEDULER_INTERVAL", "30"))  # сек между тиками шедулера


def webapp_url(base_url: str) -> str:
    return (WEBAPP_URL or (base_url.rstrip("/") + "/app")).rstrip("/")

# All production workers must use the same strong secret; rotate to revoke URLs.
MEDIA_SIGNING_SECRET = os.getenv("MEDIA_SIGNING_SECRET", "")
MEDIA_URL_TTL_SECONDS = int(os.getenv("MEDIA_URL_TTL_SECONDS", "900"))
if not MEDIA_SIGNING_SECRET:
    if not ALLOW_ANON:
        raise RuntimeError("MEDIA_SIGNING_SECRET is required in production")
    import secrets as _secrets
    MEDIA_SIGNING_SECRET = _secrets.token_urlsafe(32)  # development only

SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "1") == "1"

POLICY_VERSION = os.getenv("POLICY_VERSION", "2026-11-04")
OPERATOR_NAME = os.getenv("OPERATOR_NAME", "Оператор не настроен (тестовый стенд)")
OPERATOR_INN = os.getenv("OPERATOR_INN", "не указан")
OPERATOR_CONTACT = os.getenv("OPERATOR_CONTACT", SUPPORT_CONTACT)
HOSTING_REGION = os.getenv("HOSTING_REGION", "не подтверждён")

AUDIT_RETENTION_DAYS = max(1, int(os.getenv("AUDIT_RETENTION_DAYS", "90")))
MONITOR_TOKEN = os.getenv("MONITOR_TOKEN", "")  # read-only metrics credential, not an admin token
