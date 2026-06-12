"""PostgreSQL engine factory bound to ``DATABASE_URL``."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine

from persistence.errors import PersistenceConfigurationError

REQUIRED_SCHEME = "postgresql+psycopg"


def create_engine_from_env() -> Engine:
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
