"""LangChain tools for podcast script and audio generation."""

import os
from pathlib import Path

from langchain_core.tools import tool

import make_mp3
from generate_script import (
    PROJECT_DIR,
    PROVIDERS,
    extract_pdf_text,
    generate_script,
    load_env_file,
    resolve_source,
)


@tool("generate_podcast_script")
def generate_podcast_script_tool(
    source_pdf: str,
    script_output_path: str,
    provider: str,
    model: str = "",
    topic: str = "",
    conversation_style: str = "",
    suggestions: str = "",
) -> str:
    """Extract a PDF and save a source-grounded, customized podcast script."""
    load_env_file(PROJECT_DIR / ".env")
    try:
        api_env, model_env, default_model, dspy_provider = PROVIDERS[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported language-model provider: {provider}.") from exc

    api_key = os.environ.get(api_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{api_env} is missing from .env.")

    source_path = resolve_source(Path(source_pdf))
    source_text = extract_pdf_text(source_path)
    print(
        f"Extracted {len(source_text):,} characters from {source_path.name}.",
        flush=True,
    )
    preferences = "\n".join(
        f"{label}: {value.strip()}"
        for label, value in (
            ("Conversation style", conversation_style),
            ("Additional suggestions", suggestions),
        )
        if value.strip()
    )
    script = generate_script(
        dspy_provider,
        model.strip() or os.environ.get(model_env, "").strip() or default_model,
        api_key,
        source_text,
        topic.strip() or None,
        preferences,
    )

    destination = Path(script_output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(script, encoding="utf-8")
    return str(destination.resolve())


@tool("make_podcast_mp3")
def make_podcast_mp3_tool(
    script_path: str,
    audio_output_path: str,
    tts_provider: str = "edge",
    tts_model: str = "",
    speaker_1_voice: str = "",
    speaker_2_voice: str = "",
    rate: str = "+15%",
) -> str:
    """Synthesize a podcast script into an MP3 using the selected TTS provider."""
    load_env_file(PROJECT_DIR / ".env")
    selected_model = (
        tts_model.strip()
        or os.environ.get("GEMINI_TTS_MODEL", "").strip()
        or "gemini-3.8-flash-tts"
    )
    destination = make_mp3.convert_to_mp3(
        script_path,
        audio_output_path,
        tts_provider=tts_provider,
        tts_model=selected_model,
        speaker_1_voice=speaker_1_voice.strip() or None,
        speaker_2_voice=speaker_2_voice.strip() or None,
        rate=rate,
    )
    return str(destination.resolve())
