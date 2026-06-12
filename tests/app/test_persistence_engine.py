import pytest

from persistence import PersistenceConfigurationError, create_engine_from_env


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
