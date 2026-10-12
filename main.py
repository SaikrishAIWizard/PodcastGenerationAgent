"""Compatibility entry point; use ``python -m podcast_generator``."""

from podcast_generator.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
