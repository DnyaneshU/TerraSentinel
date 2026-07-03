"""Configuration: defaults, environment, and an optional `.terrasentinel.yml`.

Precedence (highest wins): explicit CLI flag > environment variable > config file
> built-in default. The CLI resolves flag-level overrides; this module resolves
env + config-file + defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_MAX_TOKENS = 8000
DEFAULT_FRAMEWORKS = ["terraform"]
DEFAULT_FAIL_ON = "high"
CONFIG_FILENAMES = (".terrasentinel.yml", ".terrasentinel.yaml")


@dataclass
class Settings:
    api_key: str | None
    model: str
    max_tokens: int
    frameworks: list[str] = field(default_factory=lambda: list(DEFAULT_FRAMEWORKS))
    ignore: list[str] = field(default_factory=list)
    fail_on: str = DEFAULT_FAIL_ON
    config_path: str | None = None

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


def load_settings(
    config_path: str | None = None,
    target: str | Path = ".",
    model_override: str | None = None,
) -> Settings:
    """Load settings from env + an optional config file (does not apply CLI flags)."""
    load_dotenv(override=False)
    cfg_path, cfg = _load_config_file(config_path, target)

    model = model_override or os.getenv("TERRASENTINEL_MODEL") or cfg.get("model") or DEFAULT_MODEL
    max_tokens = int(
        os.getenv("TERRASENTINEL_MAX_TOKENS") or cfg.get("max_tokens") or DEFAULT_MAX_TOKENS
    )
    frameworks = _as_list(cfg.get("frameworks")) or list(DEFAULT_FRAMEWORKS)
    ignore = _as_list(cfg.get("ignore"))
    fail_on = cfg.get("fail_on") or DEFAULT_FAIL_ON

    return Settings(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        model=model,
        max_tokens=max_tokens,
        frameworks=frameworks,
        ignore=ignore,
        fail_on=fail_on,
        config_path=cfg_path,
    )


def _load_config_file(
    explicit: str | None, target: str | Path
) -> tuple[str | None, dict]:
    """Find and parse a `.terrasentinel.yml`. Returns (path, data). Never raises."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    base = Path(target)
    for d in (base if base.is_dir() else base.parent, Path(".")):
        for name in CONFIG_FILENAMES:
            candidates.append(d / name)

    seen: set[Path] = set()
    for c in candidates:
        if c in seen or not c.is_file():
            seen.add(c)
            continue
        seen.add(c)
        try:
            import yaml

            data = yaml.safe_load(c.read_text(encoding="utf-8")) or {}
        except Exception:
            return None, {}
        if isinstance(data, dict):
            return str(c).replace("\\", "/"), data
        return None, {}
    return None, {}


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []
