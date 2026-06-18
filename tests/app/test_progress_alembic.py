"""PostgreSQL-only schema tests for `0005_progress_events`.

Runs against ``TEST_DATABASE_URL`` (a ``postgresql+psycopg://`` URL).
Skipped when the variable is unset so the default ``pytest`` run stays
green without database access.

Covers:
- the revision applies cleanly on top of the prior head;
- the `stage` / `status` CHECK constraints reject unknown values at the DB;
- the `progress_snapshots` primary key rejects duplicate
  `(shard, challenge_id)` pairs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[2]
REVISION = "0005_progress_events"

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


def _reset_schema(url: str) -> None:
    """Drop everything in `public` so the upgrade can start from scratch.

    Listing tables individually is brittle as new migrations land; resetting
    the whole schema keeps this test forward-compatible.
    """
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture
def alembic_env() -> dict[str, str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    return env


@pytest.fixture
def upgraded(alembic_env):
    url = alembic_env["DATABASE_URL"]
    _reset_schema(url)
    _run_alembic("upgrade", REVISION, env=alembic_env)
    try:
        yield url
    finally:
        _reset_schema(url)


def test_revision_creates_progress_tables_and_indexes(upgraded):
    engine = create_engine(upgraded)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "progress_events" in tables
        assert "progress_snapshots" in tables

        ev_indexes = {idx["name"] for idx in inspector.get_indexes("progress_events")}
        # The ordinary plus the partial "claims" index.
        assert "ix_progress_events_shard_id" in ev_indexes
        assert "ix_progress_events_challenge_id" in ev_indexes
        assert "ix_progress_events_claims" in ev_indexes
    finally:
        engine.dispose()


def test_invalid_stage_is_rejected_at_database(upgraded):
    engine = create_engine(upgraded)
    try:
        with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO progress_events "
                        "(shard, stage, status, percent) "
                        "VALUES ('s.json', 'cleanup', 'running', 50)"
                    )
                )
    finally:
        engine.dispose()


def test_invalid_status_is_rejected_at_database(upgraded):
    engine = create_engine(upgraded)
    try:
        with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO progress_events "
                        "(shard, stage, status, percent) "
                        "VALUES ('s.json', 'build', 'nonsense', 50)"
                    )
                )
    finally:
        engine.dispose()


def test_snapshot_primary_key_rejects_duplicate(upgraded):
    engine = create_engine(upgraded)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO progress_snapshots "
                    "(shard, challenge_id, stage, status, percent) "
                    "VALUES ('s.json', 'c', 'build', 'running', 69)"
                )
            )
        with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO progress_snapshots "
                        "(shard, challenge_id, stage, status, percent) "
                        "VALUES ('s.json', 'c', 'document', 'passed', 96)"
                    )
                )
    finally:
        engine.dispose()
