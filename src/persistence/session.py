"""Session factory and transactional context manager."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from persistence.engine import create_engine_from_env
from persistence.errors import PersistenceConnectionError


class SessionFactory:
    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine or create_engine_from_env()
        self._sessionmaker = sessionmaker(bind=self._engine, expire_on_commit=False)

    def __call__(self) -> Session:
        return self._sessionmaker()

    @property
    def engine(self) -> Engine:
        return self._engine


_default_factory: SessionFactory | None = None


def _factory() -> SessionFactory:
    global _default_factory
    if _default_factory is None:
        _default_factory = SessionFactory()
    return _default_factory


@contextmanager
def transaction(factory: SessionFactory | None = None) -> Iterator[Session]:
    session = (factory or _factory())()
    try:
        try:
            session.connection()
        except OperationalError as exc:
            raise PersistenceConnectionError(
                "Failed to connect to PostgreSQL"
            ) from exc
        try:
            yield session
        except BaseException:
            session.rollback()
            raise
        else:
            session.commit()
    finally:
        session.close()
