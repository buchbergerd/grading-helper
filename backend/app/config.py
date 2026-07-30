"""Application settings.

Deliberately stdlib-only (``dataclasses`` + ``os.environ``): ``pydantic-settings`` is not a
dependency of this project and this milestone must not add one.

Deployment context (SPECIFICATION.md §13): the container serves plain HTTP behind a department
reverse proxy that terminates TLS, so ``cookie_secure`` defaults to ``False`` and must be turned
on explicitly (``GRADINGHELPER_COOKIE_SECURE=1``) if the app is ever exposed directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})

DEFAULT_DATABASE_URL = "sqlite:///./data/gradinghelper.db"
DEFAULT_SESSION_LIFETIME_HOURS = 24
DEFAULT_SESSION_COOKIE_NAME = "gh_session"
DEFAULT_COOKIE_SECURE = False


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None else value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # fail loudly rather than silently using the default
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"Environment variable {name} must be a boolean, got {raw!r}")


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration, read once from the environment."""

    database_url: str = DEFAULT_DATABASE_URL
    session_lifetime_hours: int = DEFAULT_SESSION_LIFETIME_HOURS
    session_cookie_name: str = DEFAULT_SESSION_COOKIE_NAME
    cookie_secure: bool = DEFAULT_COOKIE_SECURE

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=_env_str("GRADINGHELPER_DATABASE_URL", DEFAULT_DATABASE_URL),
            session_lifetime_hours=_env_int(
                "GRADINGHELPER_SESSION_LIFETIME_HOURS", DEFAULT_SESSION_LIFETIME_HOURS
            ),
            session_cookie_name=_env_str(
                "GRADINGHELPER_SESSION_COOKIE_NAME", DEFAULT_SESSION_COOKIE_NAME
            ),
            cookie_secure=_env_bool("GRADINGHELPER_COOKIE_SECURE", DEFAULT_COOKIE_SECURE),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings.from_env()
