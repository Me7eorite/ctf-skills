from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from persistence import PersistenceConnectionError
from persistence.session import SessionFactory, transaction


def _factory_yielding(session: MagicMock) -> SessionFactory:
    factory = MagicMock(spec=SessionFactory)
    factory.return_value = session
    return factory


def test_transaction_commits_on_success():
    session = MagicMock()
    factory = _factory_yielding(session)

    with transaction(factory=factory) as yielded:
        assert yielded is session

    session.connection.assert_called_once_with()
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_transaction_rolls_back_and_propagates_on_exception():
    session = MagicMock()
    factory = _factory_yielding(session)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction(factory=factory):
            raise Boom("boom")

    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
    session.close.assert_called_once_with()


def test_transaction_wraps_connect_failure_as_typed_error():
    session = MagicMock()
    underlying = OperationalError("SELECT 1", {}, Exception("refused"))
    session.connection.side_effect = underlying
    factory = _factory_yielding(session)

    with pytest.raises(PersistenceConnectionError) as excinfo:
        with transaction(factory=factory):
            pytest.fail("body should not run")

    assert excinfo.value.__cause__ is underlying
    session.commit.assert_not_called()
    session.close.assert_called_once_with()
