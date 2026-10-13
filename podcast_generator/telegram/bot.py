"""Telegram conversation handling, file processing, and podcast delivery."""

import asyncio
import logging
import os
import shutil
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..settings import (
    PREFERENCES_FILE,
    PROVIDERS,
    load_preferences,
    positive_int_setting,
    provider_models,
)
from .client import (
    ProviderValidationError,
    TelegramApiError,
    TelegramClient,
    validate_provider_key,
)
from .generation import (
    estimate_generation_seconds,
    generate_audio_file,
    generate_script_file,
    read_source_text,
    run_sync_with_progress,
    safe_basename,
)
from .state import TelegramSession
from .usage import UsageStoreError, get_usage, record_successful_delivery

logger = logging.getLogger(__name__)

UPDATE_DEDUPE_LIMIT = 20_000
UPDATE_DEDUPE_TTL_SECONDS = 24 * 60 * 60
MAX_AUDIO_DELIVERY_ATTEMPTS = 3
PDF_TASK_GUIDANCE = {
    "exam": (
        "Prepare a source-grounded exam revision lesson. Identify the key "
        "concepts and distinctions, explain why answers are correct, and "
        "include practice questions with answers based only on the source. "
        "Do not claim to predict actual exam questions."
    ),
    "research": (
        "Explain the research paper for a learner. Cover its research question, "
        "motivation, methods, evidence, findings, and stated limitations. "
        "Define technical terms and clearly say when a detail is not provided "
        "in the source; do not invent context or results."
    ),
    "interview": (
        "Prepare a job seeker for interviews using this source. Explain the "
        "relevant concepts and generate likely practice interview questions "
        "with clear, source-grounded sample answers. Do not invent personal "
        "work experience or claim the questions will appear in an interview."
    ),
    "concepts": (
        "Teach the source's concepts step by step for a student or employee. "
        "Explain definitions, how ideas connect, and source-supported examples; "
        "finish with a concise recap and knowledge-check questions with answers. "
        "Do not add unsupported facts."
    ),
}


class TelegramPodcastBot:
    """Manage isolated, in-memory Telegram user sessions and podcast jobs."""

    def __init__(self, client: TelegramClient) -> None:
        self.client = client
        self.sessions: dict[int, TelegramSession] = {}
        self.active_tasks: dict[int, asyncio.Task[None]] = {}
        self._session_updated: dict[int, float] = {}
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._expiry_tasks: dict[int, asyncio.Task[None]] = {}
        self._seen_updates: OrderedDict[int, float] = OrderedDict()
        self._updates_lock = asyncio.Lock()

    async def process_update(self, update: dict[str, Any]) -> None:
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            return
        if await self._is_duplicate(update_id):
            return

        callback = update.get("callback_query")
        if isinstance(callback, dict):
            sender = callback.get("from")
            user_id = sender.get("id") if isinstance(sender, dict) else None
            if isinstance(user_id, int):
                async with self._user_lock(user_id):
                    try:
                        await self._handle_callback(callback)
                    finally:
                        self._schedule_session_expiry(user_id)
            return
        message = update.get("message")
        if isinstance(message, dict):
            identity = self._private_user(message)
            if identity is not None:
                async with self._user_lock(identity[0]):
                    try:
                        await self._handle_message(message)
                    finally:
                        self._schedule_session_expiry(identity[0])

    def _user_lock(self, user_id: int) -> asyncio.Lock:
        lock = self._user_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[user_id] = lock
        return lock

    def _schedule_session_expiry(self, user_id: int) -> None:
        session = self.sessions.get(user_id)
        if session is None:
            return
        existing = self._expiry_tasks.get(user_id)
        if existing is not None and not existing.done():
            existing.cancel()
        self._expiry_tasks[user_id] = asyncio.create_task(
            self._expire_after_idle(user_id, session)
        )

    async def _expire_after_idle(
        self, user_id: int, session: TelegramSession
    ) -> None:
        current_task = asyncio.current_task()
        try:
            ttl = positive_int_setting("TELEGRAM_SESSION_TTL_SECONDS", 30 * 60)
            while self.sessions.get(user_id) is session:
                last_activity = self._session_updated.get(user_id, time.monotonic())
                remaining = ttl - (time.monotonic() - last_activity)
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                active = self.active_tasks.get(user_id)
                if active is not None and not active.done():
                    await asyncio.sleep(min(ttl, 30))
                    continue
                self.sessions.pop(user_id, None)
                self._session_updated.pop(user_id, None)
                session.provider_key = ""
                session.gemini_key = ""
                await self._cleanup_session_files(session)
                try:
                    await self.client.call(
                        "sendMessage",
                        chat_id=session.chat_id,
                        text="This session expired due to inactivity. Send /start to begin again.",
                    )
                except TelegramApiError:
                    pass
                return
        except asyncio.CancelledError:
            return
        finally:
            if self._expiry_tasks.get(user_id) is current_task:
                self._expiry_tasks.pop(user_id, None)

    async def _is_duplicate(self, update_id: int) -> bool:
        now = time.monotonic()
        async with self._updates_lock:
            expired = [
                identifier
                for identifier, received_at in self._seen_updates.items()
                if now - received_at > UPDATE_DEDUPE_TTL_SECONDS
            ]
            for identifier in expired:
                self._seen_updates.pop(identifier, None)
            if update_id in self._seen_updates:
                return True
            self._seen_updates[update_id] = now
            while len(self._seen_updates) > UPDATE_DEDUPE_LIMIT:
                self._seen_updates.popitem(last=False)
        await self._expire_sessions(now)
        return False

    async def _expire_sessions(self, now: float) -> None:
        ttl = positive_int_setting("TELEGRAM_SESSION_TTL_SECONDS", 30 * 60)
        for user_id, updated_at in list(self._session_updated.items()):
            if (
                now - updated_at <= ttl
                or user_id in self.active_tasks
                or self.sessions.get(user_id, None) is None
            ):
                continue
            session = self.sessions.pop(user_id)
            self._session_updated.pop(user_id, None)
            session.provider_key = ""
            session.gemini_key = ""
            await self._cleanup_session_files(session)

    @staticmethod
    def _private_user(message: dict[str, Any]) -> tuple[int, int] | None:
        chat = message.get("chat")
        sender = message.get("from")
        if (
            not isinstance(chat, dict)
            or chat.get("type") != "private"
            or not isinstance(sender, dict)
            or not isinstance(sender.get("id"), int)
            or not isinstance(chat.get("id"), int)
        ):
            return None
        return sender["id"], chat["id"]

    @staticmethod
    def _username(message: dict[str, Any]) -> str:
        sender = message.get("from")
        username = sender.get("username") if isinstance(sender, dict) else None
        return username if isinstance(username, str) else ""

    async def _handle_message(self, message: dict[str, Any]) -> None:
        identity = self._private_user(message)
        if identity is None:
            return
        user_id, chat_id = identity
        self._session_updated[user_id] = time.monotonic()
        text = message.get("text")
        command = (
            text.split(maxsplit=1)[0].partition("@")[0]
            if isinstance(text, str)
            else ""
        )
        if command == "/start":
            await self._start(user_id, chat_id, username=self._username(message))
            return
        if command in {"/settings", "/configure", "/devicesettings"}:
            await self._show_device_settings(chat_id)
            return
        if command in {"/usage", "/stats"}:
            await self._show_usage(chat_id, user_id)
            return
        if command == "/keys":
            await self._show_api_key_links(chat_id)
            return
        if command == "/help":
            await self._show_help(chat_id)
            return
        if command in {"/pdf2podcast", "/script2podcast"}:
            await self._start(
                user_id,
                chat_id,
                mode="pdf" if command == "/pdf2podcast" else "script",
                username=self._username(message),
            )
            return
        if command == "/cancel":
            await self._cancel(user_id, chat_id)
            return

        session = self.sessions.get(user_id)
        if session is None:
            await self._send_start_hint(chat_id)
            return

        if session.stage in {"provider_key", "gemini_key", "gemini_tts_key"}:
            if not isinstance(text, str):
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text="Please send the API key as a text message, or use the available button.",
                )
                return
            await self._handle_key_message(session, message, text)
            return
        if session.stage == "custom_task_prompt":
            if not isinstance(text, str) or not text.strip():
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text="Please send a non-empty custom podcast instruction.",
                )
                return
            prompt = text.strip()
            if len(prompt) > 2000:
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text="Keep the custom instruction under 2,000 characters.",
                )
                return
            session.task_guidance = prompt
            await self._ask_for_upload(session)
            return
        if session.stage == "custom_model":
            if not isinstance(text, str) or not text.strip():
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text="Enter a non-empty model identifier.",
                )
                return
            selected_model = text.strip()
            if len(selected_model) > 200 or not all(
                character.isalnum() or character in "._:/-_"
                for character in selected_model
            ):
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text="That model identifier contains unsupported characters.",
                )
                return
            session.model = selected_model
            await self._after_model_selection(session)
            return
        if session.stage in {
            "conversation_style",
            "speaker_1_voice",
            "speaker_2_voice",
            "suggestions",
        }:
            if not isinstance(text, str):
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text="Please reply with text or choose Skip.",
                )
                return
            await self._handle_preference(session, text)
            return
        if session.stage in {"upload_pdf", "upload_txt"}:
            document = message.get("document")
            if not isinstance(document, dict):
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text="Please upload the requested document as a file attachment.",
                )
                return
            await self._begin_file_processing(session, document)
            return
        if session.stage == "preparing_file":
            await self.client.call(
                "sendMessage",
                chat_id=chat_id,
                text="I’m still checking your uploaded file. Please wait.",
            )
            return
        if session.stage == "generating":
            await self.client.call(
                "sendMessage",
                chat_id=chat_id,
                text="Your podcast is already being generated. Send /cancel to stop it.",
            )
            return
        await self.client.call(
            "sendMessage",
            chat_id=chat_id,
            text="Please choose one of the buttons to continue.",
        )

    async def _start(
        self,
        user_id: int,
        chat_id: int,
        mode: str | None = None,
        username: str = "",
    ) -> None:
        old_task = self.active_tasks.get(user_id)
        if old_task and not old_task.done():
            old_task.cancel()
        old_session = self.sessions.pop(user_id, None)
        expiry_task = self._expiry_tasks.pop(user_id, None)
        if expiry_task is not None:
            expiry_task.cancel()
        if old_session:
            old_session.provider_key = ""
            old_session.gemini_key = ""
            if old_task is None or old_task.done():
                await self._cleanup_session_files(old_session)

        session = TelegramSession(
            user_id=user_id,
            chat_id=chat_id,
            username=username,
            stage="choose_mode",
            provider="groq",
            model=provider_models("groq")[0],
            tts_provider="gemini",
        )
        self.sessions[user_id] = session
        self._session_updated[user_id] = time.monotonic()
        await self.client.call(
            "sendMessage",
            chat_id=chat_id,
            text=(
                "Welcome to Podcast Generator! Quick start uses Groq for the "
                "script and Gemini for voices. Choose a file type; you can "
                "change provider, model, voice, and saved device settings with "
                "/settings."
            ),
        )
        if mode is None:
            await self._show_modes(session)
        else:
            session.mode = mode
            if mode == "pdf":
                await self._show_pdf_goals(session)
            else:
                await self._ask_for_upload(session)

    async def _show_pdf_goals(self, session: TelegramSession) -> None:
        session.stage = "choose_pdf_goal"
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text=(
                "What should this PDF podcast help you with? The default keeps "
                "the usual complete, source-grounded explanation. Choose a "
                "learning goal or provide your own instruction."
            ),
            reply_markup=self._keyboard(
                [("General podcast (default)", "pdfgoal:general")],
                [("Exam preparation", "pdfgoal:exam")],
                [("Research paper explanation", "pdfgoal:research")],
                [("Job interview preparation", "pdfgoal:interview")],
                [("Understand the concepts", "pdfgoal:concepts")],
                [("Write my own prompt", "pdfgoal:custom")],
            ),
        )

    async def _ask_for_upload(self, session: TelegramSession) -> None:
        extension = "PDF" if session.mode == "pdf" else "TXT"
        session.stage = "upload_pdf" if session.mode == "pdf" else "upload_txt"
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text=f"Upload your {extension} file as a document attachment.",
        )

    async def _show_generation_options(self, session: TelegramSession) -> None:
        session.stage = "configure"
        webapp_url = self._webapp_url()
        rows: list[list[dict[str, Any]]] = []
        if webapp_url:
            rows.append(
                [
                    self._web_app_button(
                        "Use saved settings & generate", webapp_url
                    )
                ]
            )
        rows.append(
            [
                {
                    "text": "Configure in chat (fallback)",
                    "callback_data": "setup:chat",
                }
            ]
        )
        if webapp_url:
            text = (
                "Ready to generate. Use your device-saved provider, model, "
                "Gemini TTS choice, and API keys. Edit those settings with "
                "/settings. Keys are sent only for this job and kept in memory."
            )
        else:
            text = "Ready to generate. Configure the provider, model, and keys in chat."
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text=text,
            reply_markup={"inline_keyboard": rows},
        )

    async def _show_device_settings(self, chat_id: int) -> None:
        url = self._webapp_url()
        if not url:
            await self.client.call(
                "sendMessage",
                chat_id=chat_id,
                text=(
                    "Device-local settings need a public HTTPS "
                    "TELEGRAM_WEBHOOK_URL ending in /telegram/webhook. "
                    "For now, send /start and use chat setup."
                ),
            )
            return
        await self.client.call(
            "sendMessage",
            chat_id=chat_id,
            text=(
                "On supported Telegram apps, settings and API keys are saved "
                "in this device's encrypted Telegram SecureStorage. They are "
                "sent to the bot only when you use them for a podcast. Use "
                "/start to create one; older apps can use the in-chat fallback."
            ),
            reply_markup={
                "inline_keyboard": [
                    [self._web_app_button("Open device settings", url)]
                ]
            },
        )

    async def _show_usage(self, chat_id: int, user_id: int) -> None:
        counts = await get_usage(user_id)
        await self.client.call(
            "sendMessage",
            chat_id=chat_id,
            text=(
                "Successful podcast deliveries\n"
                f"All users: {counts['total_successful_deliveries']}\n"
                f"You: {counts['user_successful_deliveries']}"
            ),
        )

    async def _show_api_key_links(self, chat_id: int) -> None:
        await self.client.call(
            "sendMessage",
            chat_id=chat_id,
            text=(
                "Create or manage your API keys with the providers. "
                "The bot cannot create keys for you. Keep keys private; "
                "enter them only in the secure settings page or bot chat."
            ),
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": "Groq API keys",
                            "url": "https://console.groq.com/keys",
                        }
                    ],
                    [
                        {
                            "text": "OpenAI API keys",
                            "url": "https://platform.openai.com/api-keys",
                        }
                    ],
                    [
                        {
                            "text": "Gemini API keys",
                            "url": "https://aistudio.google.com/app/apikey",
                        }
                    ],
                ]
            },
        )

    async def _show_help(self, chat_id: int) -> None:
        await self.client.call(
            "sendMessage",
            chat_id=chat_id,
            text=(
                "How to make a podcast\n"
                "1. Send /start, choose PDF or script, and upload a PDF or TXT.\n"
                "2. Open device settings or choose the in-chat setup.\n"
                "3. Add a provider API key and start generation.\n\n"
                "Commands: /pdf2podcast, /script2podcast, /devicesettings, "
                "/keys, /usage, /cancel. Gemini TTS needs a Gemini key; "
                "Edge TTS does not. Use /keys for official provider key pages."
            ),
        )

    @staticmethod
    def _webapp_url() -> str:
        from ..settings import PROJECT_DIR, load_env_file

        load_env_file(PROJECT_DIR / ".env")
        configured_url = os.environ.get("TELEGRAM_WEBHOOK_URL", "").strip()
        if not configured_url:
            return ""
        parsed = urlsplit(configured_url)
        suffix = "/telegram/webhook"
        if parsed.scheme != "https" or not parsed.path.rstrip("/").endswith(suffix):
            return ""
        prefix = parsed.path.rstrip("/")[: -len(suffix)]
        return urlunsplit(
            (parsed.scheme, parsed.netloc, f"{prefix}/telegram/webapp", "", "")
        )

    @staticmethod
    def _web_app_button(text: str, url: str) -> dict[str, Any]:
        return {"text": text, "web_app": {"url": url}}

    async def _send_start_hint(self, chat_id: int) -> None:
        await self.client.call(
            "sendMessage",
            chat_id=chat_id,
            text="Send /start to begin a new podcast.",
        )

    async def _cancel(self, user_id: int, chat_id: int) -> None:
        task = self.active_tasks.get(user_id)
        if task is not None and not task.done():
            task.cancel()
        session = self.sessions.pop(user_id, None)
        self._session_updated.pop(user_id, None)
        expiry_task = self._expiry_tasks.pop(user_id, None)
        if expiry_task is not None:
            expiry_task.cancel()
        if session is not None:
            session.provider_key = ""
            session.gemini_key = ""
            if task is None or task.done():
                await self._cleanup_session_files(session)
        await self.client.call(
            "sendMessage",
            chat_id=chat_id,
            text=(
                "Cancellation requested. Any active provider call will be allowed "
                "to finish safely before its temporary files are removed."
                if task is not None and not task.done()
                else "The current podcast session was cancelled."
            ),
        )

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback.get("id")
        if isinstance(callback_id, str):
            try:
                await self.client.call("answerCallbackQuery", callback_query_id=callback_id)
            except TelegramApiError:
                pass
        message = callback.get("message")
        if not isinstance(message, dict):
            return
        identity = self._private_user(
            {"chat": message.get("chat"), "from": callback.get("from")}
        )
        if identity is None:
            return
        user_id, chat_id = identity
        self._session_updated[user_id] = time.monotonic()
        data = callback.get("data")
        if not isinstance(data, str):
            return

        session = self.sessions.get(user_id)
        if data == "setup:chat" and session is not None:
            session.stage = "choose_provider"
            await self.client.call(
                "sendMessage",
                chat_id=chat_id,
                text="Choose the script provider for this podcast:",
                reply_markup=self._keyboard(
                    [("Groq", "provider:groq"), ("Gemini", "provider:gemini")],
                    [("OpenAI", "provider:openai")],
                ),
            )
            return

        if data.startswith("provider:"):
            provider = data.partition(":")[2]
            if provider not in PROVIDERS:
                return
            if session is None or session.source_path is None:
                session = TelegramSession(
                    user_id=user_id,
                    chat_id=chat_id,
                    stage="provider_key",
                    provider=provider,
                )
                self.sessions[user_id] = session
            else:
                session.stage = "provider_key"
                session.provider = provider
                session.model = provider_models(provider)[0]
                session.provider_key = ""
                session.gemini_key = ""
            await self.client.call(
                "sendMessage",
                chat_id=chat_id,
                text=(
                    f"Send your {provider.title()} API key. I’ll validate it and "
                    "keep it only in this active bot session. You must use your "
                    "own key; the server does not provide provider keys."
                ),
            )
            return

        session = self.sessions.get(user_id)
        if session is None:
            await self._send_start_hint(chat_id)
            return

        if data.startswith("model:") and session.stage == "choose_model":
            choice = data.partition(":")[2]
            _, models = provider_models(session.provider)
            if choice == "custom":
                session.stage = "custom_model"
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text="Send the model ID you want to use.",
                )
            elif choice.isdigit() and int(choice) < len(models):
                session.model = models[int(choice)]
                await self._after_model_selection(session)
        elif data == "gemini:skip" and session.stage == "gemini_key":
            if session.source_path is not None:
                session.tts_provider = "edge"
                await self._show_tts_options(session)
            else:
                await self._show_modes(session)
        elif data.startswith("mode:") and session.stage == "choose_mode":
            mode = data.partition(":")[2]
            if mode not in {"pdf", "script"}:
                return
            session.mode = mode
            if mode == "pdf":
                await self._show_pdf_goals(session)
            else:
                await self._ask_for_upload(session)
        elif data.startswith("pdfgoal:") and session.stage == "choose_pdf_goal":
            goal = data.partition(":")[2]
            if goal == "custom":
                session.stage = "custom_task_prompt"
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text=(
                        "Send your custom instruction in one message "
                        "(up to 2,000 characters). I’ll keep the explanation "
                        "grounded in the uploaded PDF."
                    ),
                )
            elif goal == "general":
                session.task_guidance = ""
                await self._ask_for_upload(session)
            elif goal in PDF_TASK_GUIDANCE:
                session.task_guidance = PDF_TASK_GUIDANCE[goal]
                await self._ask_for_upload(session)
        elif data == "customize:no" and session.stage == "customize_choice":
            session.preferences = load_preferences(PREFERENCES_FILE)
            await self._show_generation_options(session)
        elif data == "customize:yes" and session.stage == "customize_choice":
            session.preferences = load_preferences(PREFERENCES_FILE)
            session.stage = "conversation_style"
            await self.client.call(
                "sendMessage",
                chat_id=chat_id,
                text=(
                    "What conversation style would you like? For example: "
                    "educational, casual, storytelling, or interview."
                ),
                reply_markup=self._keyboard([("Use default", "prefskip:conversation_style")]),
            )
        elif data.startswith("prefskip:"):
            field = data.partition(":")[2]
            if session.stage == field:
                await self._advance_preference(session, field, "")
        elif data == "tts:edge" and session.stage == "choose_tts":
            session.tts_provider = "edge"
            if session.source_path is not None:
                await self._start_generation(session)
            else:
                await self._show_modes(session)
        elif data == "tts:gemini" and session.stage == "choose_tts":
            if session.gemini_key:
                session.tts_provider = "gemini"
                if session.source_path is not None:
                    await self._start_generation(session)
                else:
                    await self._show_modes(session)
            else:
                session.stage = "gemini_tts_key"
                await self.client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text=(
                        "Send your Gemini API key to use Gemini TTS, or choose "
                        "Edge TTS instead. The key will be kept only for this session."
                    ),
                    reply_markup=self._keyboard([("Use Edge TTS", "tts:edge")]),
                )
        elif data == "tts:edge" and session.stage == "gemini_tts_key":
            session.tts_provider = "edge"
            await self._start_generation(session)

    async def _handle_key_message(
        self, session: TelegramSession, message: dict[str, Any], raw_key: str
    ) -> None:
        api_key = raw_key.strip()
        deleted = await self._delete_sensitive_message(session.chat_id, message)
        if not deleted:
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text=(
                    "I could not remove that key message automatically. Please "
                    "delete it from this chat after setup."
                ),
            )

        if api_key.casefold() == "/skip":
            if session.stage == "provider_key":
                await self.client.call(
                    "sendMessage",
                    chat_id=session.chat_id,
                    text="Please provide your own API key to continue.",
                )
                return
            elif session.stage == "gemini_key":
                if session.source_path is not None:
                    session.tts_provider = "edge"
                    await self._show_tts_options(session)
                else:
                    await self._show_modes(session)
                return
            else:
                await self.client.call(
                    "sendMessage",
                    chat_id=session.chat_id,
                    text="Gemini TTS needs a Gemini key. Send it or switch to Edge TTS.",
                    reply_markup=self._keyboard([("Use Edge TTS", "tts:edge")]),
                )
                return

        target_provider = (
            session.provider if session.stage == "provider_key" else "gemini"
        )
        try:
            await validate_provider_key(target_provider, api_key)
        except ProviderValidationError as exc:
            await self.client.call(
                "sendMessage", chat_id=session.chat_id, text=str(exc)
            )
            return

        if target_provider == session.provider:
            session.provider_key = api_key
        if target_provider == "gemini":
            session.gemini_key = api_key

        if session.stage == "provider_key":
            await self._show_model_options(session)
        elif session.stage == "gemini_key":
            if session.source_path is not None:
                await self._show_tts_options(session)
            else:
                await self._show_modes(session)
        elif session.stage == "gemini_tts_key":
            session.tts_provider = "gemini"
            await self._start_generation(session)

    async def _delete_sensitive_message(
        self, chat_id: int, message: dict[str, Any]
    ) -> bool:
        message_id = message.get("message_id")
        if not isinstance(message_id, int):
            return False
        try:
            await self.client.call(
                "deleteMessage", chat_id=chat_id, message_id=message_id
            )
            return True
        except TelegramApiError:
            return False

    async def _show_modes(self, session: TelegramSession) -> None:
        session.stage = "choose_mode"
        rows: list[list[dict[str, Any]]] = [
            [
                {"text": "PDF to Podcast", "callback_data": "mode:pdf"},
                {"text": "Script to Podcast", "callback_data": "mode:script"},
            ]
        ]
        webapp_url = self._webapp_url()
        if webapp_url:
            rows.append(
                [self._web_app_button("Device settings", webapp_url)]
            )
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text=(
                "Choose how to create your podcast. Quick start uses Groq for "
                "the script and Gemini TTS for voices."
            ),
            reply_markup={"inline_keyboard": rows},
        )

    async def _show_model_options(self, session: TelegramSession) -> None:
        default_model, models = provider_models(session.provider)
        session.stage = "choose_model"
        options = [
            (
                f"{model}{' (default)' if model == default_model else ''}",
                f"model:{index}",
            )
            for index, model in enumerate(models)
        ]
        options.append(("Enter a different model", "model:custom"))
        rows = [
            options[index:index + 2]
            for index in range(0, len(options), 2)
        ]
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text="Choose a model or enter a custom model ID:",
            reply_markup=self._keyboard(*rows),
        )

    async def _after_model_selection(self, session: TelegramSession) -> None:
        if session.provider == "gemini":
            session.gemini_key = session.provider_key
            if session.source_path is not None:
                await self._show_tts_options(session)
            else:
                await self._show_modes(session)
            return
        session.stage = "gemini_key"
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text=(
                "A Gemini API key is optional unless you choose Gemini TTS. "
                "Send your own Gemini key now, or continue without it."
            ),
            reply_markup=self._keyboard([("Continue without Gemini", "gemini:skip")]),
        )

    async def _begin_file_processing(
        self, session: TelegramSession, document: dict[str, Any]
    ) -> None:
        filename = document.get("file_name")
        file_id = document.get("file_id")
        expected_extension = ".pdf" if session.mode == "pdf" else ".txt"
        if not isinstance(filename, str) or Path(
            filename.replace("\\", "/")
        ).suffix.casefold() != expected_extension:
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text=f"Please upload a {expected_extension[1:].upper()} file.",
            )
            return
        if not isinstance(file_id, str):
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text="Telegram did not provide a valid file reference. Please upload again.",
            )
            return
        max_file_size = positive_int_setting(
            "TELEGRAM_MAX_FILE_SIZE", 20 * 1024 * 1024
        )
        file_size = document.get("file_size")
        if isinstance(file_size, int) and file_size > max_file_size:
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text="The file exceeds the configured upload size limit.",
            )
            return

        temporary_directory = Path(tempfile.mkdtemp(prefix="podcast-telegram-"))
        source_path = temporary_directory / safe_basename(filename, expected_extension)
        session.temporary_directory = temporary_directory
        session.source_path = source_path
        session.original_filename = Path(filename.replace("\\", "/")).name
        session.stage = "preparing_file"
        task = asyncio.create_task(
            self._prepare_file(session, file_id, source_path, max_file_size)
        )
        self.active_tasks[session.user_id] = task
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text="File received. I’m downloading and checking it now.",
        )

    async def _prepare_file(
        self,
        session: TelegramSession,
        file_id: str,
        source_path: Path,
        max_file_size: int,
    ) -> None:
        try:
            metadata = await self.client.call("getFile", file_id=file_id)
            remote_path = metadata.get("file_path")
            if not isinstance(remote_path, str) or not remote_path:
                raise ValueError("Telegram could not retrieve that file.")
            await self.client.download_file(remote_path, source_path, max_file_size)
            text = await run_sync_with_progress(
                lambda: read_source_text(source_path),
                progress=lambda: self._send_progress(
                    session.chat_id,
                    "File checking is taking longer than expected; I’m still working.",
                ),
                interval_seconds=30,
            )
            if len(text) > positive_int_setting(
                "TELEGRAM_MAX_TEXT_CHARACTERS", 200_000
            ):
                raise ValueError("The file contains more text than this bot allows.")
            if self.sessions.get(session.user_id) is not session:
                return
            session.input_characters = len(text)
            session.stage = "customize_choice"
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text=(
                    f"File checked ({len(text):,} characters). Would you like "
                    "to customize the podcast?"
                ),
                reply_markup=self._keyboard(
                    [("Yes, customize", "customize:yes")],
                    [("No, use defaults", "customize:no")],
                ),
            )
        except asyncio.CancelledError:
            raise
        except ValueError as exc:
            if self.sessions.get(session.user_id) is session:
                session.stage = "upload_pdf" if session.mode == "pdf" else "upload_txt"
                session.source_path = None
                session.original_filename = ""
                await self.client.call(
                    "sendMessage", chat_id=session.chat_id, text=str(exc)
                )
        except TelegramApiError as exc:
            cause = exc.__cause__
            if cause is None:
                logger.warning(
                    "Telegram file preparation failed during %s (HTTP %s).",
                    exc.method,
                    exc.status_code,
                )
            else:
                logger.warning(
                    "Telegram file preparation failed during %s "
                    "(transport error: %s).",
                    exc.method,
                    type(cause).__name__,
                )
            if self.sessions.get(session.user_id) is session:
                session.stage = "upload_pdf" if session.mode == "pdf" else "upload_txt"
                session.source_path = None
                await self.client.call(
                    "sendMessage",
                    chat_id=session.chat_id,
                    text=(
                        "Telegram could not retrieve or download that file right "
                        "now. Please try uploading it again shortly."
                    ),
                )
        except Exception as exc:
            logger.warning("Telegram file preparation failed (%s).", type(exc).__name__)
            if self.sessions.get(session.user_id) is session:
                session.stage = "upload_pdf" if session.mode == "pdf" else "upload_txt"
                session.source_path = None
                await self.client.call(
                    "sendMessage",
                    chat_id=session.chat_id,
                    text="I could not read that file. Please check it and upload it again.",
                )
        finally:
            current = self.active_tasks.get(session.user_id)
            if current is asyncio.current_task():
                self.active_tasks.pop(session.user_id, None)
            if (
                self.sessions.get(session.user_id) is not session
                or session.stage != "customize_choice"
            ):
                await self._cleanup_session_files(session)

    async def _handle_preference(self, session: TelegramSession, text: str) -> None:
        field = session.stage
        value = "" if text.strip().casefold() == "/skip" else text.strip()
        await self._advance_preference(session, field, value)

    async def _advance_preference(
        self, session: TelegramSession, field: str, value: str
    ) -> None:
        if field in session.preferences:
            if value:
                session.preferences[field] = value
        sequence = [
            ("conversation_style", "What conversation style would you like?"),
            ("speaker_1_voice", "Optional: what voice would you like for speaker 1?"),
            ("speaker_2_voice", "Optional: what voice would you like for speaker 2?"),
            ("suggestions", "Optional: any additional suggestions or topics to emphasize?"),
        ]
        index = next(
            (position for position, (name, _) in enumerate(sequence) if name == field),
            -1,
        )
        next_index = index + 1
        if next_index >= len(sequence):
            await self._show_generation_options(session)
            return
        next_field, prompt = sequence[next_index]
        session.stage = next_field
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text=prompt,
            reply_markup=self._keyboard([("Skip", f"prefskip:{next_field}")]),
        )

    async def _show_tts_options(self, session: TelegramSession) -> None:
        session.stage = "choose_tts"
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text=(
                "Choose voice generation. Gemini TTS is recommended when you "
                "have a Gemini API key. Edge TTS is the fallback and may take "
                "longer, depending on script length and network conditions."
            ),
            reply_markup=self._keyboard(
                [("Gemini TTS", "tts:gemini")],
                [("Edge TTS", "tts:edge")],
            ),
        )

    async def _start_generation(self, session: TelegramSession) -> None:
        if session.tts_provider == "gemini" and not session.gemini_key:
            session.stage = "gemini_tts_key"
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text="A valid Gemini API key is required for Gemini TTS.",
                reply_markup=self._keyboard([("Use Edge TTS", "tts:edge")]),
            )
            return
        if session.source_path is None or session.temporary_directory is None:
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text="Your upload is no longer available. Please send /start and upload it again.",
            )
            return

        estimate = estimate_generation_seconds(
            session.input_characters, session.tts_provider
        )
        estimate_text = (
            f"{max(1, round(estimate / 60))} minute(s)"
            if estimate >= 60
            else f"{estimate} seconds"
        )
        session.stage = "generating"
        await self.client.call(
            "sendMessage",
            chat_id=session.chat_id,
            text=(
                f"Estimated generation time: about {estimate_text}. This is an "
                "estimate, not a guarantee. I’ll send progress updates while "
                "I create the script and audio."
            ),
        )
        task = asyncio.create_task(self._generate_and_deliver(session, estimate))
        self.active_tasks[session.user_id] = task

    async def apply_webapp_configuration(
        self, user_id: int, configuration: dict[str, Any]
    ) -> None:
        async with self._user_lock(user_id):
            session = self.sessions.get(user_id)
            if (
                session is None
                or session.chat_id != user_id
                or session.stage != "configure"
                or session.source_path is None
            ):
                raise ValueError(
                    "No podcast is waiting for settings. Send /start and upload a file first."
                )

            provider = configuration.get("provider")
            model = configuration.get("model")
            tts_provider = configuration.get("tts_provider")
            tts_model = configuration.get("tts_model", "")
            api_key = configuration.get("api_key")
            gemini_key = configuration.get("gemini_api_key", "")
            preferences = configuration.get("preferences", {})
            if provider not in PROVIDERS or tts_provider not in {"gemini", "edge"}:
                raise ValueError("Choose a supported provider and voice option.")
            if (
                not isinstance(model, str)
                or not model.strip()
                or len(model.strip()) > 200
                or not all(
                    character.isalnum() or character in "._:/-_"
                    for character in model.strip()
                )
            ):
                raise ValueError("Enter a valid model identifier.")
            if (
                not isinstance(tts_model, str)
                or len(tts_model.strip()) > 200
                or (
                    tts_model.strip()
                    and not all(
                        character.isalnum() or character in "._:/-_"
                        for character in tts_model.strip()
                    )
                )
            ):
                raise ValueError("Enter a valid Gemini TTS model identifier.")
            if not isinstance(api_key, str) or not api_key.strip():
                raise ValueError("Enter an API key for the selected script provider.")
            if not isinstance(gemini_key, str):
                raise ValueError("The Gemini API key is invalid.")
            if not isinstance(preferences, dict):
                raise ValueError("Podcast preferences must be an object.")
            allowed_preferences = {
                "conversation_style",
                "speaker_1_voice",
                "speaker_2_voice",
                "suggestions",
            }
            if preferences.keys() - allowed_preferences or any(
                not isinstance(value, str) or len(value) > 2000
                for value in preferences.values()
            ):
                raise ValueError("One or more podcast preferences are invalid.")

            api_key = api_key.strip()
            gemini_key = gemini_key.strip()
            await validate_provider_key(provider, api_key)
            if provider == "gemini":
                gemini_key = gemini_key or api_key
            if tts_provider == "gemini":
                if not gemini_key:
                    raise ValueError(
                        "A Gemini API key is required for Gemini TTS. Add one or select Edge TTS."
                    )
                if provider != "gemini" or gemini_key != api_key:
                    await validate_provider_key("gemini", gemini_key)

            session.provider = provider
            session.model = model.strip()
            session.tts_model = tts_model.strip()
            session.provider_key = api_key
            session.gemini_key = gemini_key
            session.tts_provider = tts_provider
            for field, value in preferences.items():
                if value.strip():
                    session.preferences[field] = value.strip()
            await self._start_generation(session)

    async def _generate_and_deliver(
        self, session: TelegramSession, estimate_seconds: int
    ) -> None:
        temporary_directory = session.temporary_directory
        assert temporary_directory is not None
        script_path = temporary_directory / "podcast_script.md"
        audio_path = temporary_directory / safe_basename(
            session.original_filename, ".mp3"
        )
        delivered = False
        started_at = time.monotonic()
        job_timeout = positive_int_setting(
            "TELEGRAM_JOB_TIMEOUT_SECONDS", 30 * 60
        )
        try:
            source_path = session.source_path
            if source_path is None:
                raise ValueError("The source upload is no longer available.")
            source_text = await run_sync_with_progress(
                lambda: read_source_text(source_path),
                progress=lambda: self._send_progress(
                    session.chat_id,
                    "Source text extraction is still in progress.",
                ),
            )
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text="Generating the podcast conversation script now.",
            )
            suggestions = "\n\n".join(
                prompt
                for prompt in (
                    session.task_guidance,
                    session.preferences["suggestions"],
                )
                if prompt.strip()
            )
            script_work = lambda: generate_script_file(
                source_text,
                script_path,
                session.provider,
                session.provider_key,
                session.preferences["conversation_style"],
                suggestions,
                session.model,
                job_timeout,
            )
            await run_sync_with_progress(
                script_work,
                progress=lambda: self._send_progress(
                    session.chat_id,
                    "Script generation is taking longer than expected; I’m still working.",
                ),
                interval_seconds=45,
            )
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text=(
                    "The script is ready. Generating podcast voices now"
                    + (" with Gemini TTS." if session.tts_provider == "gemini" else " with Edge TTS.")
                ),
            )
            audio_work = lambda: generate_audio_file(
                script_path,
                audio_path,
                session.tts_provider,
                session.gemini_key,
                session.preferences["speaker_1_voice"],
                session.preferences["speaker_2_voice"],
                max(0.01, job_timeout - (time.monotonic() - started_at)),
                tts_model=session.tts_model,
            )
            await run_sync_with_progress(
                audio_work,
                progress=lambda: self._send_progress(
                    session.chat_id,
                    "Voice generation is taking longer than expected; I’m still working.",
                ),
                interval_seconds=45,
            )

            elapsed = int(time.monotonic() - started_at)
            if elapsed > estimate_seconds:
                await self.client.call(
                    "sendMessage",
                    chat_id=session.chat_id,
                    text="Generation took longer than the estimate, but the MP3 is ready.",
                )
            await self._send_audio_with_retries(session.chat_id, audio_path)
            delivered = True
            try:
                counts = await record_successful_delivery(
                    session.user_id, session.username
                )
            except UsageStoreError as exc:
                logger.error(
                    "Audio was delivered but usage could not be recorded (%s).",
                    type(exc).__name__,
                )
                delivery_message = (
                    "Your podcast MP3 was delivered successfully, but the usage "
                    "counter could not be saved."
                )
            else:
                delivery_message = (
                    "Your podcast MP3 was delivered successfully.\n"
                    f"All users: {counts['total_successful_deliveries']} "
                    "successful deliveries; you: "
                    f"{counts['user_successful_deliveries']}."
                )
            await self.client.call(
                "sendMessage",
                chat_id=session.chat_id,
                text=delivery_message,
            )
        except asyncio.CancelledError:
            try:
                await self.client.call(
                    "sendMessage",
                    chat_id=session.chat_id,
                    text="Podcast generation was cancelled. Temporary files are being removed.",
                )
            except TelegramApiError:
                pass
            raise
        except TelegramApiError as exc:
            cause = exc.__cause__
            if cause is None:
                logger.warning(
                    "Telegram podcast generation failed during %s (HTTP %s).",
                    exc.method,
                    exc.status_code,
                )
            else:
                logger.warning(
                    "Telegram podcast generation failed during %s "
                    "(transport error: %s).",
                    exc.method,
                    type(cause).__name__,
                )
            try:
                await self.client.call(
                    "sendMessage",
                    chat_id=session.chat_id,
                    text=self._generation_error_message(exc),
                )
            except TelegramApiError:
                pass
        except Exception as exc:
            logger.warning("Telegram podcast generation failed (%s).", type(exc).__name__)
            try:
                await self.client.call(
                    "sendMessage",
                    chat_id=session.chat_id,
                    text=self._generation_error_message(exc),
                )
            except TelegramApiError:
                pass
        finally:
            if audio_path.is_file() and not delivered:
                logger.info("Removing undelivered Telegram podcast output.")
            await self._cleanup_session_files(session)
            session.provider_key = ""
            session.gemini_key = ""
            current_task = asyncio.current_task()
            if self.sessions.get(session.user_id) is session:
                self.sessions.pop(session.user_id, None)
                self._session_updated.pop(session.user_id, None)
                expiry_task = self._expiry_tasks.get(session.user_id)
                if expiry_task is not None and expiry_task is not current_task:
                    expiry_task.cancel()
            if self.active_tasks.get(session.user_id) is current_task:
                self.active_tasks.pop(session.user_id, None)

    @staticmethod
    def _generation_error_message(exc: Exception) -> str:
        status_code = getattr(exc, "status_code", None)
        if status_code == 429 or type(exc).__name__ == "RateLimitError":
            return "The AI or voice provider is rate limiting requests. Please try again later."
        if isinstance(exc, ValueError):
            return (
                "Could not process this podcast request. Check the uploaded "
                "content and provider settings, then try again."
            )
        return (
            "Podcast generation failed because a provider or processing step "
            "was unavailable. Check your selected provider/key and try again."
        )

    async def _send_audio_with_retries(self, chat_id: int, audio_path: Path) -> None:
        output_limit = positive_int_setting(
            "TELEGRAM_MAX_AUDIO_SIZE", 50 * 1024 * 1024
        )
        if audio_path.stat().st_size > output_limit:
            raise ValueError("The generated MP3 exceeds Telegram's configured send limit.")

        last_error: TelegramApiError | None = None
        for attempt in range(MAX_AUDIO_DELIVERY_ATTEMPTS):
            try:
                await self.client.send_audio(
                    chat_id,
                    audio_path,
                    audio_path.name,
                )
                return
            except TelegramApiError as exc:
                last_error = exc
                if (
                    not exc.retryable
                    or exc.__cause__ is not None
                    or attempt == MAX_AUDIO_DELIVERY_ATTEMPTS - 1
                ):
                    break
                delay = min(exc.retry_after or 2**attempt, 20)
                await asyncio.sleep(delay)
        if last_error is not None:
            raise last_error

    async def _cleanup_session_files(self, session: TelegramSession) -> None:
        directory = session.temporary_directory
        session.temporary_directory = None
        session.source_path = None
        if directory is None:
            return
        try:
            await asyncio.to_thread(shutil.rmtree, directory)
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning(
                "Could not clean a Telegram temporary directory (%s).",
                type(exc).__name__,
            )

    async def _send_progress(self, chat_id: int, text: str) -> None:
        try:
            await self.client.call("sendMessage", chat_id=chat_id, text=text)
        except TelegramApiError as exc:
            logger.warning("Could not send Telegram progress update (%s).", exc.status_code)

    @staticmethod
    def _keyboard(*rows: tuple[str, str] | list[tuple[str, str]]) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in row]
                for row in rows
            ]
        }
