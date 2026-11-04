"""Read-only production configuration checks. Does not print secret values."""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


def check(env):
    errors = []
    if env.get("ALLOW_ANON") != "0":
        errors.append("Set ALLOW_ANON=0")
    for key in ("ADMIN_TOKEN", "MEDIA_SIGNING_SECRET", "WEBHOOK_SECRET"):
        if len(env.get(key, "")) < 32:
            errors.append(f"{key}: at least 32 random characters required")
    if env.get("MONITOR_TOKEN"):
        if len(env["MONITOR_TOKEN"]) < 32 or env["MONITOR_TOKEN"] == env.get("ADMIN_TOKEN"):
            errors.append("MONITOR_TOKEN must be a separate random secret of at least 32 characters")
    if not env.get("BOT_TOKEN"):
        errors.append("BOT_TOKEN is required for Telegram production use")
    parsed = urlparse(env.get("PUBLIC_URL", ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname in ("localhost", "127.0.0.1"):
        errors.append("PUBLIC_URL must be an external HTTPS URL")
    if bool(env.get("YUKASSA_SHOP_ID")) != bool(env.get("YUKASSA_SECRET_KEY")):
        errors.append("Set both payment credentials or neither (payments disabled)")
    if not env.get("SUPPORT_CONTACT") or env.get("SUPPORT_CONTACT") == "@scena_support":
        errors.append("Configure your real SUPPORT_CONTACT")
    for key in ("OPERATOR_NAME", "OPERATOR_INN", "OPERATOR_CONTACT", "HOSTING_REGION"):
        if not env.get(key):
            errors.append(f"Configure {key}; legal/hosting facts must be supplied by operator")
    return errors


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    errors = check(os.environ)
    for error in errors:
        print("FAIL:", error)
    if errors:
        sys.exit(1)
    print("PREFLIGHT OK (configuration only; not live payment, DNS or legal verification)")
