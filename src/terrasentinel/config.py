"""Configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_MAX_TOKENS = 8000


@dataclass
class Settings:
    api_key: str | None
    model: str
    max_tokens: int

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


def load_settings(model_override: str | None = None) -> Settings:
    """Load settings, reading a local .env if present (does not override real env)."""
    load_dotenv(override=False)
    return Settings(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        model=model_override or os.getenv("TERRASENTINEL_MODEL", DEFAULT_MODEL),
        max_tokens=int(os.getenv("TERRASENTINEL_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
    )
