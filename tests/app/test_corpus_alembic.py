"""PostgreSQL-only schema tests for corpus-governance migration."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]
REVISION = "0022_corpus_governance"

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


def test_corpus_governance_revision_creates_expected_tables_and_indexes(alembic_env):
    url = alembic_env["DATABASE_URL"]
    _reset_schema(url)
    try:
        _run_alembic("upgrade", REVISION, env=alembic_env)
        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert {
                "corpus_batches",
                "corpus_batch_members",
                "corpus_decisions",
                "corpus_matches",
                "observation_review_decisions",
                "corpus_review_decisions",
                "corpus_history_entries",
            }.issubset(tables)
            decision_indexes = {
                idx["name"] for idx in inspector.get_indexes("corpus_decisions")
            }
            assert "uq_corpus_decisions_current_member" in decision_indexes
            assert "uq_corpus_decisions_current_aggregate" in decision_indexes
            member_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("corpus_batch_members")
            }
            assert "uq_corpus_batch_members_batch_attempt" in member_uniques
            assert "uq_corpus_batch_members_batch_evidence" in member_uniques
        finally:
            engine.dispose()
        _run_alembic("downgrade", "-1", env=alembic_env)
        _run_alembic("upgrade", REVISION, env=alembic_env)
    finally:
        _reset_schema(url)
