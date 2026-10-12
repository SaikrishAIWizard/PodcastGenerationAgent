"""FastAPI route for Telegram's authenticated webhook updates."""

import asyncio
import logging
import os
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..settings import PROJECT_DIR, load_env_file
from .bot import TelegramPodcastBot
from .client import TelegramClient, TelegramApiError

logger = logging.getLogger(__name__)
router = APIRouter()


def get_bot(request: Request) -> TelegramPodcastBot:
    """Lazily initialize the bot after loading local environment settings."""
    bot = getattr(request.app.state, "telegram_bot", None)
    if bot is not None:
        return bot
    load_env_file(PROJECT_DIR / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Telegram bot integration is not configured.",
        )
    bot = TelegramPodcastBot(TelegramClient(token))
    request.app.state.telegram_bot = bot
    return bot


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict[str, bool]:
    """Accept Telegram updates and schedule long-running generation in background."""
    load_env_file(PROJECT_DIR / ".env")
    configured_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not configured_secret:
        raise HTTPException(
            status_code=503,
            detail="Telegram webhook secret is not configured.",
        )
    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(received_secret, configured_secret):
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret.")

    try:
        update: Any = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON update.") from exc
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Telegram update must be a JSON object.")

    bot = get_bot(request)
    tasks = getattr(request.app.state, "telegram_webhook_tasks", None)
    if tasks is None:
        tasks = set()
        request.app.state.telegram_webhook_tasks = tasks
    task = asyncio.create_task(_process_update(bot, update))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"ok": True}


async def _process_update(
    bot: TelegramPodcastBot, update: dict[str, Any]
) -> None:
    try:
        await bot.process_update(update)
    except TelegramApiError as exc:
        cause = exc.__cause__
        if cause is None:
            logger.warning(
                "Telegram update handling failed during %s (HTTP %s).",
                exc.method,
                exc.status_code,
            )
        else:
            logger.warning(
                "Telegram update handling failed during %s (transport error: %s).",
                exc.method,
                type(cause).__name__,
            )
    except Exception as exc:
        logger.warning("Telegram update handling failed (%s).", type(exc).__name__)
