"""Compatibility imports for the podcast script-generation service."""

from podcast_generator.cli import main, parse_args
from podcast_generator.services.scripts import (
    SYSTEM_PROMPT,
    extract_pdf_text,
    generate_script,
    normalize_script,
    resolve_source,
)
from podcast_generator.settings import (
    INPUT_DIR,
    OUTPUT_DIR,
    PREFERENCES_FILE,
    PROJECT_DIR,
    PROVIDERS,
    load_env_file,
    load_preferences,
)

__all__ = [
    "INPUT_DIR",
    "OUTPUT_DIR",
    "PREFERENCES_FILE",
    "PROJECT_DIR",
    "PROVIDERS",
    "SYSTEM_PROMPT",
    "extract_pdf_text",
    "generate_script",
    "load_env_file",
    "load_preferences",
    "main",
    "normalize_script",
    "parse_args",
    "resolve_source",
]


if __name__ == "__main__":
    raise SystemExit(main())
