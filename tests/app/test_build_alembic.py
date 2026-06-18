"""PostgreSQL-only schema tests for `0006_build_attempts`."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[2]
REVISION = "0006_build_attempts"

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


@pytest.fixture
def upgraded(alembic_env):
    url = alembic_env["DATABASE_URL"]
    _reset_schema(url)
    _run_alembic("upgrade", REVISION, env=alembic_env)
    try:
        yield url
    finally:
        _reset_schema(url)


def _insert_design_task(conn, *, status: str = "designed"):
    request_id = uuid4()
    run_id = uuid4()
    task_id = uuid4()
    task_no = int(str(task_id.int)[-6:]) + 1
    conn.execute(
        text(
            "INSERT INTO generation_requests "
            "(id, category, topic, target_count, difficulty_distribution, status) "
            "VALUES (:id, 'web', 'SQL injection', 1, '{\"easy\": 1}'::jsonb, 'researched')"
        ),
        {"id": request_id},
    )
    conn.execute(
        text(
            "INSERT INTO research_runs "
            "(id, generation_request_id, attempt, status) "
            "VALUES (:id, :request_id, 1, 'completed')"
        ),
        {"id": run_id, "request_id": request_id},
    )
    conn.execute(
        text(
            "INSERT INTO design_tasks "
            "(id, generation_request_id, research_run_id, task_no, challenge_id, "
            "title, category, difficulty, primary_technique, learning_objective, "
            "points, status) "
            "VALUES (:id, :request_id, :run_id, :task_no, :challenge_id, "
            "'Demo', 'web', 'easy', 'boolean blind', 'Practice SQLi', 100, :status)"
        ),
        {
            "id": task_id,
            "request_id": request_id,
            "run_id": run_id,
            "task_no": task_no,
            "challenge_id": f"web-{task_no}",
            "status": status,
        },
    )
    return task_id


def test_revision_applies_and_downgrade_upgrade_cycle_is_clean(alembic_env):
    url = alembic_env["DATABASE_URL"]
    _reset_schema(url)
    try:
        _run_alembic("upgrade", REVISION, env=alembic_env)
        _run_alembic("downgrade", "-1", env=alembic_env)
        _run_alembic("upgrade", "head", env=alembic_env)

        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert "build_attempts" in tables
            indexes = {idx["name"] for idx in inspector.get_indexes("build_attempts")}
            assert "one_active_build_per_task" in indexes
            assert "ix_build_attempts_status_created" in indexes
            assert "ix_build_attempts_shard" in indexes
        finally:
            engine.dispose()
    finally:
        _reset_schema(url)


def test_unknown_build_attempt_status_is_rejected(upgraded):
    engine = create_engine(upgraded)
    try:
        with engine.begin() as conn:
            task_id = _insert_design_task(conn)
        with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO build_attempts "
                        "(id, design_task_id, attempt_no, status, shard_basename) "
                        "VALUES (:id, :task_id, 1, 'mystery', 'web-0001.json')"
                    ),
                    {"id": uuid4(), "task_id": task_id},
                )
    finally:
        engine.dispose()


def test_partial_unique_index_rejects_second_active_attempt(upgraded):
    engine = create_engine(upgraded)
    try:
        with engine.begin() as conn:
            task_id = _insert_design_task(conn)
            conn.execute(
                text(
                    "INSERT INTO build_attempts "
                    "(id, design_task_id, attempt_no, status, shard_basename) "
                    "VALUES (:id, :task_id, 1, 'queued', 'web-0001.json')"
                ),
                {"id": uuid4(), "task_id": task_id},
            )
        with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO build_attempts "
                        "(id, design_task_id, attempt_no, status, shard_basename) "
                        "VALUES (:id, :task_id, 2, 'running', 'web-0002.json')"
                    ),
                    {"id": uuid4(), "task_id": task_id},
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize("status", ["building", "built", "build_failed"])
def test_design_tasks_status_check_admits_build_phase_values(upgraded, status):
    engine = create_engine(upgraded)
    try:
        with engine.begin() as conn:
            _insert_design_task(conn, status=status)
    finally:
        engine.dispose()
