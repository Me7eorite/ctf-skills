"""PostgreSQL persistence layer."""

from persistence.engine import create_engine_from_env
from persistence.errors import (
    PersistenceConfigurationError,
    PersistenceConnectionError,
)
from persistence.repositories.progress import PostgresProgressStore
from persistence.session import SessionFactory, transaction

__all__ = [
    "PersistenceConfigurationError",
    "PersistenceConnectionError",
    "SessionFactory",
    "PostgresProgressStore",
    "create_engine_from_env",
    "make_postgres_progress_store",
    "transaction",
]


def make_postgres_progress_store(
    factory: SessionFactory | None = None,
) -> PostgresProgressStore:
    return PostgresProgressStore(factory=factory)
