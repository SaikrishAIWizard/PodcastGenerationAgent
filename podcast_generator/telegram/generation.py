"""Temporary file processing and podcast generation for Telegram jobs."""

import asyncio
import math
import os
import re
from collections.abc import Awaitable
from pathlib import Path
from typing import Callable, TypeVar

from ..services import audio, scripts
from ..settings import PROVIDERS, positive_int_setting, provider_models

T = TypeVar("T")


def safe_basename(filename: str, suffix: str) -> str:
    """Build a filesystem-safe output name from a Telegram document name."""
    basename = Path(filename.replace("\\", "/")).name
    stem = Path(basename).stem
    safe_stem = re.sub(r"[^\w .-]+", "_", stem).strip(" ._")[:100]
    return f"{safe_stem or 'podcast'}{suffix}"


def read_source_text(path: Path) -> str:
    """Extract PDF text or decode a UTF-8 text upload."""
    if path.suffix.casefold() == ".pdf":
        text = scripts.extract_pdf_text(path)
    elif path.suffix.casefold() == ".txt":
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("The text file must use UTF-8 encoding.") from exc
        text = text.strip()
    else:
        raise ValueError("Upload a PDF or TXT file.")

    if not text:
        raise ValueError("The uploaded file contains no readable text.")
    max_characters = positive_int_setting("TELEGRAM_MAX_TEXT_CHARACTERS", 200_000)
    if len(text) > max_characters:
        raise ValueError(
            "The extracted text exceeds the configured character limit."
        )
    return text


def estimate_generation_seconds(
    input_characters: int, tts_provider: str
) -> int:
    """Estimate processing time from input length and TTS chunk throughput."""
    script_chunks = max(1, math.ceil(input_characters / 12_000))
    estimated_script_characters = max(input_characters, input_characters * 2)
    if tts_provider == "gemini":
        tts_chunks = max(1, math.ceil(estimated_script_characters / 12_000))
        tts_seconds = tts_chunks * 25
    else:
        tts_chunks = max(1, math.ceil(estimated_script_characters / 2_500))
        tts_seconds = tts_chunks * 12
    return max(45, 20 + script_chunks * 12 + tts_seconds)


async def run_sync_with_progress(
    function: Callable[[], T],
    *,
    progress: Callable[[], Awaitable[None]],
    interval_seconds: int = 45,
) -> T:
    """Run blocking provider work without blocking the webhook event loop."""
    worker = asyncio.create_task(asyncio.to_thread(function))
    try:
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(worker), timeout=interval_seconds
                )
            except TimeoutError:
                await progress()
    except asyncio.CancelledError:
        try:
            await asyncio.shield(worker)
        except Exception:
            pass
        raise


def generate_script_file(
    source_text: str,
    destination: Path,
    provider: str,
    api_key: str,
    conversation_style: str,
    suggestions: str,
    model: str = "",
    timeout_seconds: float | None = None,
) -> Path:
    """Generate a podcast script using the selected provider credentials."""
    _, _, _, dspy_provider = PROVIDERS[provider]
    default_model, _ = provider_models(provider)
    preferences = "\n".join(
        f"{label}: {value.strip()}"
        for label, value in (
            ("Conversation style", conversation_style),
            ("Additional suggestions", suggestions),
        )
        if value.strip()
    )
    script = scripts.generate_script(
        dspy_provider,
        model.strip() or default_model,
        api_key,
        source_text,
        None,
        preferences,
        timeout_seconds,
    )
    destination.write_text(script, encoding="utf-8")
    return destination


def generate_audio_file(
    script_path: Path,
    destination: Path,
    tts_provider: str,
    gemini_api_key: str,
    speaker_1_voice: str,
    speaker_2_voice: str,
    timeout_seconds: float | None = None,
    tts_model: str = "",
) -> Path:
    """Synthesize the generated script using per-session voice settings."""
    return audio.convert_to_mp3(
        script_path,
        destination,
        tts_provider=tts_provider,
        tts_model=tts_model.strip()
        or os.environ.get("GEMINI_TTS_MODEL", "").strip()
        or "gemini-3.8-flash-tts",
        speaker_1_voice=speaker_1_voice or None,
        speaker_2_voice=speaker_2_voice or None,
        gemini_api_key=gemini_api_key or None,
        rate="+15%",
        timeout_seconds=timeout_seconds,
    )
