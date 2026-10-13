"""FastAPI route for Telegram's authenticated webhook updates."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..settings import PROJECT_DIR, load_env_file
from .bot import TelegramPodcastBot
from .client import (
    ProviderValidationError,
    TelegramApiError,
    TelegramClient,
)
from .usage import UsageStoreError, get_usage

logger = logging.getLogger(__name__)
router = APIRouter()
WEBAPP_FILE = Path(__file__).with_name("webapp.html")
WEBAPP_INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60


class TelegramWebAppConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=200)
    tts_provider: str = Field(min_length=1, max_length=16)
    tts_model: str = Field(default="", max_length=200)
    api_key: str = Field(min_length=1, max_length=4096)
    gemini_api_key: str = Field(default="", max_length=4096)
    preferences: dict[str, str] = Field(default_factory=dict)


def _telegram_webapp_user_id(init_data: str) -> int:
    load_env_file(PROJECT_DIR / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not init_data:
        raise HTTPException(status_code=401, detail="Open this page from Telegram.")

    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    supplied_hash = fields.pop("hash", "")
    auth_date = fields.get("auth_date", "")
    user_data = fields.get("user", "")
    if not supplied_hash or not auth_date or not user_data:
        raise HTTPException(status_code=401, detail="Invalid Telegram session.")

    try:
        auth_timestamp = int(auth_date)
        user = json.loads(user_data)
        user_id = user["id"]
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid Telegram session.") from None
    now = int(time.time())
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or auth_timestamp > now + 60
        or now - auth_timestamp > WEBAPP_INIT_DATA_MAX_AGE_SECONDS
    ):
        raise HTTPException(status_code=401, detail="Telegram session expired.")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(
        b"WebAppData", token.encode("utf-8"), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied_hash, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram signature.")
    return user_id


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


@router.get("/telegram/webapp")
async def telegram_webapp() -> FileResponse:
    """Serve the device-local settings interface used inside Telegram."""
    if not WEBAPP_FILE.is_file():
        raise HTTPException(status_code=404, detail="Telegram settings page is unavailable.")
    return FileResponse(
        WEBAPP_FILE,
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/telegram/webapp/defaults")
async def telegram_webapp_defaults() -> dict[str, str]:
    """Return non-secret defaults for the device-local settings form."""
    load_env_file(PROJECT_DIR / ".env")
    return {
        "tts_model": os.environ.get("GEMINI_TTS_MODEL", "").strip()
        or "gemini-3.8-flash-tts"
    }


@router.get("/telegram/webapp/usage")
async def telegram_webapp_usage(request: Request) -> dict[str, int]:
    """Return global and personal counts to an authenticated Telegram user."""
    user_id = _telegram_webapp_user_id(
        request.headers.get("X-Telegram-Init-Data", "")
    )
    try:
        return await get_usage(user_id)
    except UsageStoreError as exc:
        raise HTTPException(
            status_code=503,
            detail="Usage totals are temporarily unavailable.",
        ) from exc


@router.post("/telegram/webapp/configure")
async def configure_from_telegram_webapp(request: Request) -> dict[str, bool]:
    """Use device-saved credentials for one active Telegram podcast job."""
    user_id = _telegram_webapp_user_id(
        request.headers.get("X-Telegram-Init-Data", "")
    )
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > 16_384:
        raise HTTPException(status_code=413, detail="Settings payload is too large.")
    try:
        body = await request.body()
        if len(body) > 16_384:
            raise HTTPException(status_code=413, detail="Settings payload is too large.")
        configuration = TelegramWebAppConfiguration.model_validate_json(body)
    except ValidationError:
        raise HTTPException(status_code=422, detail="Invalid settings.") from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid settings.") from None

    bot = get_bot(request)
    try:
        await bot.apply_webapp_configuration(user_id, configuration.model_dump())
    except ProviderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except TelegramApiError as exc:
        logger.warning(
            "Could not start Telegram Web App job during %s (HTTP %s).",
            exc.method,
            exc.status_code,
        )
        raise HTTPException(
            status_code=503,
            detail="Could not start the podcast right now. Please retry.",
        ) from None
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
