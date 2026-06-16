"""Integration tests for the research-planning Alembic revision."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]
BASELINE_REVISION = "0001_baseline"
RESEARCH_REVISION = "0002_research_tables"

pytestmark = pytest.mark.postgres

RESEARCH_TABLES = {
    "challenge_categories",
    "agent_roles",
    "generation_requests",
    "research_runs",
    "hermes_profile_bindings",
    "research_sources",
    "research_findings",
    "research_finding_sources",
}

EXPECTED_ENUMS = {
    "generation_request_status": ["draft", "researching", "researched", "failed"],
    "research_run_status": ["queued", "running", "completed", "failed"],
    "research_finding_kind": ["technique", "variant", "scenario", "prerequisite"],
}


def _run_alembic(
    *args: str,
    env: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def alembic_env() -> dict[str, str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    return env


def _drop_alembic_version(url: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        engine.dispose()


def _enum_labels(url: str, enum_name: str) -> list[str]:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            return list(
                conn.execute(
                    text(
                        """
                        SELECT e.enumlabel
                        FROM pg_type t
                        JOIN pg_enum e ON e.enumtypid = t.oid
                        WHERE t.typname = :enum_name
                        ORDER BY e.enumsortorder
                        """
                    ),
                    {"enum_name": enum_name},
                ).scalars()
            )
    finally:
        engine.dispose()


def test_research_revision_upgrade_and_downgrade_cycle(alembic_env):
    url = alembic_env["DATABASE_URL"]
    _run_alembic("downgrade", "base", env=alembic_env, check=False)
    _drop_alembic_version(url)

    try:
        _run_alembic("upgrade", RESEARCH_REVISION, env=alembic_env)
        current = _run_alembic("current", env=alembic_env)
        assert RESEARCH_REVISION in current.stdout

        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert RESEARCH_TABLES.issubset(tables)

            for enum_name, expected_labels in EXPECTED_ENUMS.items():
                assert _enum_labels(url, enum_name) == expected_labels

            with engine.begin() as conn:
                categories = conn.execute(
                    text(
                        """
                        SELECT code, display_name
                        FROM challenge_categories
                        ORDER BY code
                        """
                    )
                ).all()
                roles = conn.execute(
                    text("SELECT code FROM agent_roles ORDER BY code")
                ).scalars().all()
                bindings = conn.execute(
                    text(
                        """
                        SELECT role, profile_name, status
                        FROM hermes_profile_bindings
                        ORDER BY role
                        """
                    )
                ).all()

            assert [row.code for row in categories] == ["pwn", "re", "web"]
            assert all(row.display_name for row in categories)
            assert roles == ["research"]
            assert [(row.role, row.profile_name, row.status) for row in bindings] == [
                ("research", "default", "enabled")
            ]

            generation_columns = {
                column["name"] for column in inspector.get_columns("generation_requests")
            }
            research_run_columns = {
                column["name"] for column in inspector.get_columns("research_runs")
            }
            binding_columns = {
                column["name"]: column
                for column in inspector.get_columns("hermes_profile_bindings")
            }
            assert "seed_urls" in generation_columns
            assert {"claim_token", "profile_name_used"}.issubset(
                research_run_columns
            )
            assert binding_columns["last_used_run_id"]["nullable"] is True

            unique_constraints = inspector.get_unique_constraints("research_runs")
            assert any(
                constraint["name"] == "uq_research_runs_generation_request_attempt"
                and constraint["column_names"] == ["generation_request_id", "attempt"]
                for constraint in unique_constraints
            )

            foreign_keys = inspector.get_foreign_keys("hermes_profile_bindings")
            assert any(
                fk["constrained_columns"] == ["last_used_run_id"]
                and fk["referred_table"] == "research_runs"
                and fk["referred_columns"] == ["id"]
                and fk.get("options", {}).get("ondelete", "").upper() == "SET NULL"
                for fk in foreign_keys
            )
        finally:
            engine.dispose()

        _run_alembic("downgrade", BASELINE_REVISION, env=alembic_env)
        engine = create_engine(url)
        try:
            tables_after = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        assert tables_after - {"alembic_version"} == set()
    finally:
        _run_alembic("downgrade", "base", env=alembic_env, check=False)
        _drop_alembic_version(url)
