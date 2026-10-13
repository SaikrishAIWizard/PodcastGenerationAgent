import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from podcast_generator.api.main import app
from podcast_generator.telegram.bot import TelegramPodcastBot
from podcast_generator.telegram.client import TelegramClient
from podcast_generator.telegram.state import TelegramSession


def signed_init_data(token: str, user_id: int, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "test-query",
        "user": json.dumps({"id": user_id}, separators=(",", ":")),
    }
    check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(
        b"WebAppData", token.encode("utf-8"), hashlib.sha256
    ).digest()
    fields["hash"] = hmac.new(
        secret_key, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


class TelegramWebAppTests(unittest.TestCase):
    def setUp(self):
        self.old_bot = getattr(app.state, "telegram_bot", None)

    def tearDown(self):
        if self.old_bot is None:
            if hasattr(app.state, "telegram_bot"):
                del app.state.telegram_bot
        else:
            app.state.telegram_bot = self.old_bot

    def test_settings_page_is_served_without_caching(self):
        response = TestClient(app).get("/telegram/webapp")

        self.assertEqual(response.status_code, 200)
        self.assertIn("localStorage", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_configure_requires_valid_telegram_signature(self):
        bot = SimpleNamespace(apply_webapp_configuration=AsyncMock())
        app.state.telegram_bot = bot
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}):
            response = TestClient(app).post(
                "/telegram/webapp/configure",
                headers={"X-Telegram-Init-Data": "auth_date=1&hash=invalid"},
                json={},
            )

        self.assertEqual(response.status_code, 401)
        bot.apply_webapp_configuration.assert_not_awaited()

    def test_configure_rejects_expired_telegram_signature(self):
        token = "test-token"
        bot = SimpleNamespace(apply_webapp_configuration=AsyncMock())
        app.state.telegram_bot = bot
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": token}):
            response = TestClient(app).post(
                "/telegram/webapp/configure",
                headers={
                    "X-Telegram-Init-Data": signed_init_data(
                        token, 123, int(time.time()) - 25 * 60 * 60
                    )
                },
                json={},
            )

        self.assertEqual(response.status_code, 401)
        bot.apply_webapp_configuration.assert_not_awaited()

    def test_configure_passes_validated_telegram_user_and_settings(self):
        token = "test-token"
        bot = SimpleNamespace(apply_webapp_configuration=AsyncMock())
        app.state.telegram_bot = bot
        config = {
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "tts_provider": "gemini",
            "tts_model": "gemini-3.8-flash-tts",
            "api_key": "groq-key",
            "gemini_api_key": "gemini-key",
            "preferences": {"conversation_style": "conversational"},
        }
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": token}):
            response = TestClient(app).post(
                "/telegram/webapp/configure",
                headers={
                    "X-Telegram-Init-Data": signed_init_data(token, 123)
                },
                json=config,
            )

        self.assertEqual(response.status_code, 200, response.text)
        bot.apply_webapp_configuration.assert_awaited_once_with(123, config)


class TelegramWebAppBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_device_configuration_applies_only_to_active_job(self):
        bot = TelegramPodcastBot(TelegramClient("unused"))
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.txt"
            source.write_text("source")
            session = TelegramSession(
                user_id=123,
                chat_id=123,
                stage="configure",
                source_path=source,
            )
            bot.sessions[123] = session
            configuration = {
                "provider": "groq",
                "model": "groq-custom",
                "tts_provider": "gemini",
                "tts_model": "gemini-custom-tts",
                "api_key": "groq-secret",
                "gemini_api_key": "gemini-secret",
                "preferences": {"conversation_style": "friendly"},
            }
            with (
                patch(
                    "podcast_generator.telegram.bot.validate_provider_key",
                    new=AsyncMock(return_value=None),
                ) as validate,
                patch.object(bot, "_start_generation", new=AsyncMock()) as start,
            ):
                await bot.apply_webapp_configuration(123, configuration)

        self.assertEqual(
            validate.await_args_list,
            [
                call("groq", "groq-secret"),
                call("gemini", "gemini-secret"),
            ],
        )
        self.assertEqual(session.provider, "groq")
        self.assertEqual(session.model, "groq-custom")
        self.assertEqual(session.tts_provider, "gemini")
        self.assertEqual(session.tts_model, "gemini-custom-tts")
        self.assertEqual(session.provider_key, "groq-secret")
        self.assertEqual(session.gemini_key, "gemini-secret")
        self.assertEqual(session.preferences["conversation_style"], "friendly")
        start.assert_awaited_once_with(session)

    async def test_device_configuration_rejects_missing_active_job(self):
        bot = TelegramPodcastBot(TelegramClient("unused"))

        with self.assertRaisesRegex(ValueError, "No podcast is waiting"):
            await bot.apply_webapp_configuration(
                123,
                {
                    "provider": "groq",
                    "model": "groq-custom",
                    "tts_provider": "edge",
                    "api_key": "groq-secret",
                },
            )


if __name__ == "__main__":
    unittest.main()
