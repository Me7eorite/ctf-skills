"""PostgreSQL engine factory bound to ``DATABASE_URL``."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine

from persistence.errors import PersistenceConfigurationError

REQUIRED_SCHEME = "postgresql+psycopg"

# Project root holds the optional `.env` file (next to pyproject.toml).
# Resolved once at import time so every entry point — CLI, web server,
# alembic env.py, and tests — sees the same file.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DOTENV_PATH = _PROJECT_ROOT / ".env"
_DOTENV_LOADED = False


def _ensure_dotenv_loaded() -> None:
    """Load the project's `.env` file once if `python-dotenv` is installed.

    Never overrides values already present in `os.environ`, so explicit
    `export DATABASE_URL=...` in the shell still wins. A missing dotenv
    package or missing `.env` file is silently ignored — `.env` is
    optional convenience, not required configuration.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if _DOTENV_PATH.exists():
        load_dotenv(_DOTENV_PATH, override=False)


def create_engine_from_env() -> Engine:
    _ensure_dotenv_loaded()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise PersistenceConfigurationError(
            "DATABASE_URL is not set; persistence requires a PostgreSQL URL."
        )
    scheme = url.split("://", 1)[0] if "://" in url else url
    if scheme != REQUIRED_SCHEME:
        raise PersistenceConfigurationError(
            f"DATABASE_URL scheme {scheme!r} is not supported; "
            f"expected {REQUIRED_SCHEME!r}."
        )
    return create_engine(url, pool_pre_ping=True)
