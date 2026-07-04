"""Integration test for the Alembic baseline migration.

Runs against the database identified by ``TEST_DATABASE_URL`` (must be a
``postgresql+psycopg://`` URL). When the variable is unset, the test is
skipped so the default ``pytest`` run stays green without database access.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]
BASELINE_REVISION = "0001_baseline"

pytestmark = pytest.mark.postgres


def _run_alembic(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def alembic_env() -> dict[str, str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    return env


def _drop_all_tables(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        tables = inspect(conn).get_table_names()
        for table in tables:
            safe_table = table.replace('"', '""')
            conn.execute(text(f'DROP TABLE IF EXISTS "{safe_table}" CASCADE'))
    engine.dispose()


def test_baseline_upgrade_and_downgrade_cycle(alembic_env):
    url = alembic_env["DATABASE_URL"]
    _drop_all_tables(url)
    try:
        # Explicitly target the baseline revision rather than "head" so this
        # test stays focused on the empty no-op migration even as later
        # revisions add tables.
        _run_alembic("upgrade", BASELINE_REVISION, env=alembic_env)
        current = _run_alembic("current", env=alembic_env)
        assert BASELINE_REVISION in current.stdout

        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        assert tables.issubset({"alembic_version"})

        _run_alembic("downgrade", "base", env=alembic_env)
        engine = create_engine(url)
        try:
            tables_after = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        application_tables = tables_after - {"alembic_version"}
        assert application_tables == set()
    finally:
        _drop_all_tables(url)
