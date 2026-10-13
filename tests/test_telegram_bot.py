import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from fastapi.testclient import TestClient

from podcast_generator.api.main import app
from podcast_generator.telegram.bot import TelegramPodcastBot
from podcast_generator.telegram.client import TelegramClient
from podcast_generator.telegram.generation import (
    estimate_generation_seconds,
    read_source_text,
    safe_basename,
)
from podcast_generator.settings import provider_models


class FakeTelegramClient(TelegramClient):
    def __init__(self):
        self.calls = []

    async def call(self, method, **data):
        self.calls.append((method, data))
        if method == "getFile":
            return {"file_path": "documents/upload.txt"}
        return {}

    async def download_file(self, file_path, destination, max_bytes):
        if not file_path or max_bytes < 1:
            raise AssertionError("Expected a valid Telegram file path and size limit.")
        destination.write_text("A useful podcast source.", encoding="utf-8")
        return destination.stat().st_size

    async def send_audio(self, chat_id, audio_path, filename):
        if audio_path.stat().st_size < 1:
            raise AssertionError("Expected a non-empty MP3 file.")
        self.calls.append(("sendAudio", {"chat_id": chat_id, "filename": filename}))
        return {}


def private_message(user_id, chat_id, message_id, **payload):
    return {
        "message_id": message_id,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": user_id},
        **payload,
    }


class TelegramBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeTelegramClient()
        self.bot = TelegramPodcastBot(self.client)

    async def test_start_uses_quick_defaults_and_offers_input_modes(self):
        await self.bot.process_update(
            {
                "update_id": 1,
                "message": private_message(10, 10, 1, text="/start"),
            }
        )

        session = self.bot.sessions[10]
        self.assertEqual(session.stage, "choose_mode")
        self.assertEqual(session.provider, "groq")
        self.assertEqual(session.model, provider_models("groq")[0])
        self.assertEqual(session.tts_provider, "gemini")
        keyboard = self.client.calls[-1][1]["reply_markup"]["inline_keyboard"]
        choices = {
            button["callback_data"]
            for row in keyboard
            for button in row
            if "callback_data" in button
        }
        self.assertEqual(
            choices,
            {"mode:pdf", "mode:script"},
        )

    async def test_pdf_menu_command_starts_at_pdf_upload(self):
        await self.bot.process_update(
            {
                "update_id": 20,
                "message": private_message(10, 10, 20, text="/pdf2podcast"),
            }
        )

        session = self.bot.sessions[10]
        self.assertEqual(session.stage, "upload_pdf")
        self.assertEqual(session.mode, "pdf")
        self.assertIn("Upload your PDF", self.client.calls[-1][1]["text"])

    async def test_script_menu_command_starts_at_script_upload(self):
        await self.bot.process_update(
            {
                "update_id": 21,
                "message": private_message(10, 10, 21, text="/script2podcast"),
            }
        )

        session = self.bot.sessions[10]
        self.assertEqual(session.stage, "upload_txt")
        self.assertEqual(session.mode, "script")
        self.assertIn("Upload your TXT", self.client.calls[-1][1]["text"])

    async def test_provider_key_is_deleted_and_only_kept_in_its_user_session(self):
        callback = {
            "id": "callback",
            "from": {"id": 10},
            "message": {
                "chat": {"id": 10, "type": "private"},
            },
            "data": "provider:groq",
        }
        await self.bot.process_update({"update_id": 1, "callback_query": callback})

        with patch(
            "podcast_generator.telegram.bot.validate_provider_key",
            return_value=None,
        ) as validate:
            await self.bot.process_update(
                {
                    "update_id": 2,
                    "message": private_message(10, 10, 2, text="private-key-value"),
                }
            )

        self.assertEqual(validate.call_args.args, ("groq", "private-key-value"))
        self.assertEqual(self.bot.sessions[10].provider_key, "private-key-value")
        self.assertEqual(self.bot.sessions[10].stage, "choose_model")
        self.assertIn(
            ("deleteMessage", {"chat_id": 10, "message_id": 2}),
            self.client.calls,
        )
        self.assertNotIn("private-key-value", repr(self.client.calls))

        await self.bot.process_update(
            {
                "update_id": 3,
                "callback_query": {
                    "id": "model-choice",
                    "from": {"id": 10},
                    "message": {"chat": {"id": 10, "type": "private"}},
                    "data": "model:0",
                },
            }
        )
        self.assertEqual(
            self.bot.sessions[10].model,
            provider_models("groq")[1][0],
        )
        self.assertEqual(self.bot.sessions[10].stage, "gemini_key")

    async def test_gemini_provider_key_continues_to_mode_selection(self):
        callback = {
            "id": "callback",
            "from": {"id": 10},
            "message": {"chat": {"id": 10, "type": "private"}},
            "data": "provider:gemini",
        }
        await self.bot.process_update({"update_id": 1, "callback_query": callback})

        with patch(
            "podcast_generator.telegram.bot.validate_provider_key",
            return_value=None,
        ):
            await self.bot.process_update(
                {
                    "update_id": 2,
                    "message": private_message(10, 10, 2, text="valid-gemini-key"),
                }
            )

        self.assertEqual(self.bot.sessions[10].stage, "choose_model")
        self.assertEqual(self.bot.sessions[10].provider_key, "valid-gemini-key")
        self.assertEqual(self.bot.sessions[10].gemini_key, "valid-gemini-key")
        await self.bot.process_update(
            {
                "update_id": 3,
                "callback_query": {
                    "id": "model-choice",
                    "from": {"id": 10},
                    "message": {"chat": {"id": 10, "type": "private"}},
                    "data": "model:0",
                },
            }
        )
        self.assertEqual(self.bot.sessions[10].stage, "choose_mode")

    async def test_custom_model_is_accepted_for_provider(self):
        await self.bot.process_update(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "provider-choice",
                    "from": {"id": 10},
                    "message": {"chat": {"id": 10, "type": "private"}},
                    "data": "provider:openai",
                },
            }
        )
        with patch(
            "podcast_generator.telegram.bot.validate_provider_key",
            return_value=None,
        ):
            await self.bot.process_update(
                {
                    "update_id": 2,
                    "message": private_message(10, 10, 2, text="user-openai-key"),
                }
            )
        await self.bot.process_update(
            {
                "update_id": 3,
                "callback_query": {
                    "id": "custom-model-choice",
                    "from": {"id": 10},
                    "message": {"chat": {"id": 10, "type": "private"}},
                    "data": "model:custom",
                },
            }
        )
        await self.bot.process_update(
            {
                "update_id": 4,
                "message": private_message(
                    10, 10, 4, text="openai/gpt-custom-model"
                ),
            }
        )

        self.assertEqual(
            self.bot.sessions[10].model,
            "openai/gpt-custom-model",
        )
        self.assertEqual(self.bot.sessions[10].stage, "gemini_key")

    async def test_webhook_update_deduplication_prevents_repeated_start(self):
        update = {
            "update_id": 50,
            "message": private_message(10, 10, 1, text="/start"),
        }
        await self.bot.process_update(update)
        calls_after_first_delivery = list(self.client.calls)
        await self.bot.process_update(update)

        self.assertEqual(self.client.calls, calls_after_first_delivery)

    async def test_sessions_are_isolated_between_users(self):
        await self.bot.process_update(
            {
                "update_id": 1,
                "message": private_message(10, 10, 1, text="/start"),
            }
        )
        await self.bot.process_update(
            {
                "update_id": 2,
                "message": private_message(20, 20, 2, text="/start"),
            }
        )

        self.bot.sessions[10].provider_key = "user-ten-key"
        self.assertEqual(self.bot.sessions[20].provider_key, "")


class TelegramValidationTests(unittest.TestCase):
    def test_file_name_is_sanitized_and_extension_is_replaced(self):
        self.assertEqual(
            safe_basename("../../meeting notes.pdf", ".mp3"),
            "meeting notes.mp3",
        )

    def test_script_text_requires_utf8_and_nonempty_content(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.txt"
            source.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                read_source_text(source)
            source.write_text("  ", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no readable text"):
                read_source_text(source)

    def test_generation_estimate_scales_with_text_and_tts_provider(self):
        short = estimate_generation_seconds(100, "edge")
        long_edge = estimate_generation_seconds(30_000, "edge")
        long_gemini = estimate_generation_seconds(30_000, "gemini")

        self.assertGreater(long_edge, short)
        self.assertGreater(long_edge, long_gemini)

    def test_provider_error_message_does_not_echo_exception_content(self):
        sensitive_message = "provider failed with key: do-not-share"
        message = TelegramPodcastBot._generation_error_message(
            ValueError(sensitive_message)
        )

        self.assertNotIn("do-not-share", message)

    def test_webhook_requires_configured_secret(self):
        with patch.dict(
            "os.environ",
            {"TELEGRAM_WEBHOOK_SECRET": "valid-secret"},
            clear=False,
        ):
            app.state.telegram_bot = TelegramPodcastBot(
                TelegramClient("not-a-real-token")
            )
            response = TestClient(app).post(
                "/telegram/webhook",
                json={"update_id": 1},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
            )

        self.assertEqual(response.status_code, 403)


class TelegramMenuRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_registration_installs_menu_commands(self):
        from podcast_generator.telegram import set_webhook

        client = SimpleNamespace(call=AsyncMock(return_value={}))
        with (
            patch.dict(
                "os.environ",
                {
                    "TELEGRAM_BOT_TOKEN": "test-token",
                    "TELEGRAM_WEBHOOK_SECRET": "test-secret",
                    "TELEGRAM_WEBHOOK_URL": "https://example.com/telegram/webhook",
                },
                clear=True,
            ),
            patch.object(set_webhook, "load_env_file"),
            patch.object(set_webhook, "TelegramClient", return_value=client),
        ):
            await set_webhook.register_webhook()

        self.assertEqual(
            [entry.args[0] for entry in client.call.await_args_list],
            ["setWebhook", "setMyCommands"],
        )
        commands = client.call.await_args_list[1].kwargs["commands"]
        self.assertEqual(
            [entry["command"] for entry in commands[:3]],
            ["devicesettings", "pdf2podcast", "script2podcast"],
        )


class ProviderApiTests(unittest.TestCase):
    def test_provider_catalog_includes_env_models_and_default(self):
        with patch.dict(
            "os.environ",
            {
                "GROQ_MODEL": "groq-default",
                "GROQ_MODELS": "groq-default,groq-fast,groq-reasoning",
            },
            clear=False,
        ):
            response = TestClient(app).get("/providers")

        self.assertEqual(response.status_code, 200)
        groq = response.json()["groq"]
        self.assertEqual(groq["default_model"], "groq-default")
        self.assertEqual(
            groq["models"],
            [
                "groq-default",
                "groq-fast",
                "groq-reasoning",
                "llama-3.3-70b-versatile",
            ],
        )

    def test_api_generation_passes_user_keys_and_custom_model(self):
        from podcast_generator.api import main as api_main

        with tempfile.TemporaryDirectory() as folder:
            output_dir = Path(folder)

            def fake_invoke(state):
                script_path = Path(state["script_output_path"])
                audio_path = Path(state["audio_output_path"])
                script_path.write_text("HOST: Hi\nEXPERT: Hello\n", encoding="utf-8")
                audio_path.write_bytes(b"mp3")
                return {
                    "script_path": str(script_path),
                    "audio_path": str(audio_path),
                }

            with (
                patch.object(api_main, "OUTPUT_DIR", output_dir),
                patch.object(
                    api_main.podcast_graph, "invoke", side_effect=fake_invoke
                ) as invoke,
                patch.object(
                    api_main,
                    "validate_provider_key",
                    new=AsyncMock(return_value=None),
                ) as validate,
            ):
                response = TestClient(app).post(
                    "/podcasts",
                    files={"file": ("source.pdf", b"%PDF-test", "application/pdf")},
                    data={
                        "provider": "groq",
                        "api_key": "user-provided-secret",
                        "model": "custom-model-v2",
                    },
                )

        self.assertEqual(response.status_code, 201, response.text)
        validate.assert_awaited_once_with("groq", "user-provided-secret")
        self.assertEqual(invoke.call_args.args[0]["api_key"], "user-provided-secret")
        self.assertEqual(invoke.call_args.args[0]["model"], "custom-model-v2")
        self.assertNotIn("user-provided-secret", response.text)

    def test_api_passes_and_validates_separate_gemini_tts_key(self):
        from podcast_generator.api import main as api_main

        with tempfile.TemporaryDirectory() as folder:
            output_dir = Path(folder)

            def fake_invoke(state):
                script_path = Path(state["script_output_path"])
                audio_path = Path(state["audio_output_path"])
                script_path.write_text("HOST: Hi\nEXPERT: Hello\n", encoding="utf-8")
                audio_path.write_bytes(b"mp3")
                return {
                    "script_path": str(script_path),
                    "audio_path": str(audio_path),
                }

            with (
                patch.object(api_main, "OUTPUT_DIR", output_dir),
                patch.object(
                    api_main.podcast_graph, "invoke", side_effect=fake_invoke
                ) as invoke,
                patch.object(
                    api_main,
                    "validate_provider_key",
                    new=AsyncMock(return_value=None),
                ) as validate,
            ):
                response = TestClient(app).post(
                    "/podcasts",
                    files={"file": ("source.pdf", b"%PDF-test", "application/pdf")},
                    data={
                        "provider": "groq",
                        "api_key": "user-groq-secret",
                        "gemini_api_key": "user-gemini-secret",
                        "tts_provider": "gemini",
                    },
                )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            validate.await_args_list,
            [
                call("groq", "user-groq-secret"),
                call("gemini", "user-gemini-secret"),
            ],
        )
        self.assertEqual(
            invoke.call_args.args[0]["gemini_api_key"],
            "user-gemini-secret",
        )

    def test_api_requires_a_user_key(self):
        response = TestClient(app).post(
            "/podcasts",
            files={"file": ("source.pdf", b"%PDF-test", "application/pdf")},
            data={"provider": "groq", "api_key": "   "},
        )

        self.assertEqual(response.status_code, 422)

    def test_gemini_tts_requires_user_gemini_key(self):
        with patch(
            "podcast_generator.api.main.validate_provider_key",
            new=AsyncMock(return_value=None),
        ):
            response = TestClient(app).post(
                "/podcasts",
                files={"file": ("source.pdf", b"%PDF-test", "application/pdf")},
                data={
                    "provider": "groq",
                    "api_key": "user-groq-key",
                    "tts_provider": "gemini",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Gemini API key", response.json()["detail"])

    def test_api_errors_redact_user_credentials(self):
        with (
            patch(
                "podcast_generator.api.main.validate_provider_key",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "podcast_generator.api.main.podcast_graph.invoke",
                side_effect=RuntimeError("provider rejected user-secret-value"),
            ),
        ):
            response = TestClient(app).post(
                "/podcasts",
                files={"file": ("source.pdf", b"%PDF-test", "application/pdf")},
                data={
                    "provider": "groq",
                    "api_key": "user-secret-value",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("user-secret-value", response.text)
        self.assertIn("[redacted]", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
