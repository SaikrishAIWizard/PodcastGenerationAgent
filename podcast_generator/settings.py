"""Project paths, provider defaults, and local configuration loading."""

import json
import os
import re
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_DIR / "input"
OUTPUT_DIR = PROJECT_DIR / "output"
PREFERENCES_FILE = INPUT_DIR / "podcast_preferences.json"
TELEGRAM_WEBHOOK_SECRET_ENV = "TELEGRAM_WEBHOOK_SECRET"
TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"

PROVIDERS = {
    "groq": ("GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile", "groq"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "gemini-2.5-flash", "gemini"),
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4o-mini", "openai"),
}

PREFERENCE_FIELDS = {
    "conversation_style",
    "speaker_1_voice",
    "speaker_2_voice",
    "suggestions",
}


def provider_models(provider: str) -> tuple[str, list[str]]:
    """Return a configured default model and selectable models for a provider."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported language-model provider: {provider}.")

    _, model_env, fallback_model, _ = PROVIDERS[provider]

    def split_models(value: str) -> list[str]:
        return [
            model.strip()
            for model in re.split(r"[,\r\n]+", value)
            if model.strip()
        ]

    configured_default = split_models(os.environ.get(model_env, ""))
    configured_list = split_models(
        os.environ.get(f"{provider.upper()}_MODELS", "")
    )
    default_model = configured_default[0] if configured_default else fallback_model
    models = list(
        dict.fromkeys([*configured_default, *configured_list, fallback_model])
    )
    return default_model, models


def positive_int_setting(name: str, default: int) -> int:
    """Read a positive integer setting at runtime, after environment loading."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def load_env_file(path: Path) -> None:
    """Load KEY=value pairs from .env without overriding existing variables."""
    if not path.is_file():
        return
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid .env entry on line {line_number}.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_preferences(path: Path) -> dict[str, str]:
    """Load and validate podcast preferences from a JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid podcast preferences in {path} at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(f"Podcast preferences in {path} must be a JSON object.")

    unknown_fields = data.keys() - PREFERENCE_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"Unknown podcast preference field(s) in {path}: {names}.")

    preferences = {}
    for field in PREFERENCE_FIELDS:
        value = data.get(field, "")
        if not isinstance(value, str):
            raise ValueError(
                f"Podcast preference '{field}' in {path} must be a string."
            )
        preferences[field] = value.strip()
    return preferences
