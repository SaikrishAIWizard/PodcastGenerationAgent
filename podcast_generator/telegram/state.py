"""Ephemeral per-user state for Telegram conversations."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TelegramSession:
    user_id: int
    chat_id: int
    username: str = ""
    stage: str = "choose_provider"
    provider: str = ""
    model: str = ""
    tts_model: str = ""
    provider_key: str = ""
    gemini_key: str = ""
    mode: str = ""
    task_guidance: str = ""
    source_path: Path | None = None
    original_filename: str = ""
    temporary_directory: Path | None = None
    input_characters: int = 0
    preferences: dict[str, str] = field(
        default_factory=lambda: {
            "conversation_style": "",
            "speaker_1_voice": "",
            "speaker_2_voice": "",
            "suggestions": "",
        }
    )
    tts_provider: str = ""
