"""Command-line interface for generating a complete podcast."""

import argparse
import os
import sys
from pathlib import Path

from .services.scripts import resolve_source
from .settings import (
    OUTPUT_DIR,
    PREFERENCES_FILE,
    PROJECT_DIR,
    PROVIDERS,
    load_env_file,
    load_preferences,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a two-speaker podcast from a digital-text PDF."
    )
    parser.add_argument("source", type=Path, help="PDF file (or a filename in input/)")
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    parser.add_argument("--model", help="override the model set in .env")
    parser.add_argument("--topic", help="optional focus for the conversation")
    parser.add_argument(
        "--preferences-file",
        type=Path,
        default=PREFERENCES_FILE,
        help="JSON file with conversation and voice preferences",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--rate", default="+15%", help="speech rate, e.g. +10%%")
    parser.add_argument(
        "--tts-provider",
        choices=("edge", "gemini"),
        default="edge",
        help="speech synthesis backend (default: edge)",
    )
    parser.add_argument("--tts-model", help="override the Gemini TTS model")
    parser.add_argument("--speaker-1-voice")
    parser.add_argument("--speaker-2-voice")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        load_env_file(PROJECT_DIR / ".env")
        api_env, model_env, default_model, _ = PROVIDERS[args.provider]
        api_key = os.environ.get(api_env, "").strip()
        if not api_key:
            raise ValueError(f"{api_env} is missing from .env.")
        model = args.model or os.environ.get(model_env, "").strip() or default_model
        preferences = load_preferences(args.preferences_file.expanduser())

        source_path = resolve_source(args.source)
        output_dir = args.output_dir.expanduser().resolve()
        script_path = output_dir / f"{source_path.stem}_podcast.md"
        audio_path = output_dir / f"{source_path.stem}_podcast.mp3"
        if not args.overwrite and (script_path.exists() or audio_path.exists()):
            raise FileExistsError(
                f"Output already exists in {output_dir}; choose another output "
                "directory or pass --overwrite."
            )

        from .workflows.podcast import podcast_graph

        result = podcast_graph.invoke(
            {
                "source_pdf": str(source_path),
                "provider": args.provider,
                "model": model,
                "topic": args.topic or "",
                "conversation_style": preferences["conversation_style"],
                "suggestions": preferences["suggestions"],
                "tts_provider": args.tts_provider,
                "tts_model": args.tts_model or "",
                "speaker_1_voice": args.speaker_1_voice
                or preferences["speaker_1_voice"],
                "speaker_2_voice": args.speaker_2_voice
                or preferences["speaker_2_voice"],
                "rate": args.rate,
                "script_output_path": str(script_path),
                "audio_output_path": str(audio_path),
            }
        )
        print(f"Saved script: {Path(result['script_path'])}", flush=True)
        print(f"Saved audio: {Path(result['audio_path'])}")
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
