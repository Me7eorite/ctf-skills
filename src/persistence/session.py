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
    """ SQLAlchemy Session factory. 通过环境变量配置连接参数，默认连接 PostgreSQL。"""
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
    """ 全局默认的 SessionFactory 实例。第一次调用时创建，之后复用。 """
    global _default_factory
    if _default_factory is None:
        _default_factory = SessionFactory()
    return _default_factory


@contextmanager
def transaction(factory: SessionFactory | None = None) -> Iterator[Session]:
    """提供一个 SQLAlchemy Session，并在上下文结束时自动提交或回滚。"""
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
