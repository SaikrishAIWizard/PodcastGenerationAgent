"""Compatibility entry point; audio services live in ``podcast_generator``."""

from podcast_generator.services.audio import (
    MAX_RETRY_DELAY_SECONDS,
    MAX_SYNTHESIS_ATTEMPTS,
    SynthesisError,
    chunks,
    clean,
    convert_to_mp3,
    dialogue_turns,
    load_gemini_api_key,
    main,
    synthesize_gemini,
)

__all__ = [
    "MAX_RETRY_DELAY_SECONDS",
    "MAX_SYNTHESIS_ATTEMPTS",
    "SynthesisError",
    "chunks",
    "clean",
    "convert_to_mp3",
    "dialogue_turns",
    "load_gemini_api_key",
    "synthesize_gemini",
]


if __name__ == "__main__":
    raise SystemExit(main())
