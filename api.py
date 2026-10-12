"""Compatibility ASGI entry point; use ``podcast_generator.api.main:app``."""

from podcast_generator.api.main import app

__all__ = ["app"]
