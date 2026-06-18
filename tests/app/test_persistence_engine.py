import pytest
from sqlalchemy import create_engine

from persistence import PersistenceConfigurationError, SessionFactory, create_engine_from_env
from persistence.repositories.progress import PostgresProgressStore


@pytest.fixture(autouse=True)
def _skip_dotenv(monkeypatch):
    """Don't let project-root .env clobber `monkeypatch.delenv("DATABASE_URL")`.

    `create_engine_from_env` calls `_ensure_dotenv_loaded` which reads .env
    when present. The tests in this file probe the env-var fallback path,
    so we mark dotenv as already-loaded to make the loader a no-op.
    """
    monkeypatch.setattr("persistence.engine._DOTENV_LOADED", True)


def test_missing_database_url_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(PersistenceConfigurationError) as excinfo:
        create_engine_from_env()
    assert "DATABASE_URL" in str(excinfo.value)


@pytest.mark.parametrize(
    "url, rejected_scheme",
    [
        ("sqlite:///work/state.sqlite3", "sqlite"),
        ("postgresql://postgres:pw@localhost:5432/x", "postgresql"),
        ("mysql+pymysql://u:p@h/db", "mysql+pymysql"),
    ],
)
def test_non_psycopg_scheme_rejected(monkeypatch, url, rejected_scheme):
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(PersistenceConfigurationError) as excinfo:
        create_engine_from_env()
    assert rejected_scheme in str(excinfo.value)


def test_malformed_url_rejected(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "not-a-url-at-all")
    with pytest.raises(PersistenceConfigurationError):
        create_engine_from_env()


def test_valid_url_builds_postgres_engine(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:pw@localhost:5432/example",
    )
    engine = create_engine_from_env()
    assert engine.dialect.name == "postgresql"


def test_progress_store_redacts_database_url_password():
    engine = create_engine(
        "postgresql+psycopg://user:secret@example.test:5432/challenge_factory"
    )
    store = PostgresProgressStore(SessionFactory(engine))

    assert store._redacted_url() == (  # noqa: SLF001
        "postgresql+psycopg://user:***@example.test:5432/challenge_factory"
    )
