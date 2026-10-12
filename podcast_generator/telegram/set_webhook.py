"""Register the configured Telegram webhook with the Bot API."""

import asyncio
import os
import re

from ..settings import PROJECT_DIR, load_env_file
from .client import TelegramClient


async def register_webhook() -> None:
    load_env_file(PROJECT_DIR / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    url = os.environ.get("TELEGRAM_WEBHOOK_URL", "").strip()
    if not token or not secret or not url:
        raise ValueError(
            "Set TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, and "
            "TELEGRAM_WEBHOOK_URL in .env."
        )
    if not url.startswith("https://"):
        raise ValueError("TELEGRAM_WEBHOOK_URL must use HTTPS.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
        raise ValueError(
            "TELEGRAM_WEBHOOK_SECRET must contain 1-256 letters, numbers, "
            "underscores, or hyphens."
        )

    client = TelegramClient(token)
    await client.call(
        "setWebhook",
        url=url,
        secret_token=secret,
        allowed_updates=["message", "callback_query"],
    )
    print("Telegram webhook registered.")


def main() -> None:
    try:
        asyncio.run(register_webhook())
    except Exception as exc:
        raise SystemExit(
            f"Telegram webhook registration failed ({type(exc).__name__}). "
            "Check the webhook configuration and try again."
        ) from None


if __name__ == "__main__":
    main()
