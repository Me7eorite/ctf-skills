"""PostgreSQL-backed tests for the worker-side execution outcome recording."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

import cli
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models import executions as exec_model
from persistence.models import research as research_model
from persistence.repositories import BuildAttemptsRepository, ExecutionsRepository
from persistence.session import SessionFactory

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def session_factory() -> SessionFactory:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )
    engine = create_engine(url, pool_pre_ping=True)
    try:
        yield SessionFactory(engine)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory):
    with session_factory() as session:
        session.execute(sa.delete(exec_model.RevalidationEvent))
        session.execute(
            sa.update(build_model.BuildAttempt).values(
                current_execution_id=None,
                latest_execution_id=None,
                successful_execution_id=None,
            )
        )
        session.execute(sa.delete(exec_model.Execution))
        session.execute(sa.delete(exec_model.BuildFeedbackSnapshot))
        session.execute(sa.delete(build_model.BuildAttempt))
        session.execute(sa.delete(task_model.DesignTask))
        session.execute(sa.delete(research_model.ResearchRun))
        session.execute(sa.delete(research_model.GenerationRequest))
        session.commit()
    yield


def _seed_scheduled(session) -> tuple:
    request = research_model.GenerationRequest(
        id=uuid4(),
        category="web",
        topic=f"topic-{uuid4()}",
        target_count=1,
        difficulty_distribution={"easy": 1},
        status="researched",
    )
    run = research_model.ResearchRun(
        id=uuid4(), generation_request_id=request.id, attempt=1, status="completed"
    )
    task = task_model.DesignTask(
        id=uuid4(),
        generation_request_id=request.id,
        research_run_id=run.id,
        task_no=1,
        challenge_id=f"web-{uuid4().hex[:8]}",
        title="T",
        category="web",
        difficulty="easy",
        primary_technique="t",
        learning_objective="o",
        points=100,
        port=8080,
        scenario="",
        constraints={},
        evidence_summary="",
        finding_ids=[],
        status="building",
    )
    session.add_all([request, run, task])
    session.flush()
    attempt = BuildAttemptsRepository(session).create_attempt(task.id, "b.json")
    execution = ExecutionsRepository(session).schedule_execution(
        attempt.id, execution_kind="initial"
    )
    return attempt.id, execution.id


def test_records_succeeded_outcome(session_factory, monkeypatch):
    monkeypatch.setenv("EXECUTION_MINTING", "1")
    with session_factory() as session:
        attempt_id, execution_id = _seed_scheduled(session)
        session.commit()

    cli._mark_attempt_running(
        attempt_id,
        "worker-1",
        session_factory=session_factory,
    )
    cli._record_execution_outcome(
        attempt_id,
        "worker-1",
        {"processed": 1, "failed": 0, "outcomes": [{"status": "passed"}]},
        session_factory=session_factory,
    )

    with session_factory() as session:
        row = session.get(exec_model.Execution, execution_id)
        assert row.status == "succeeded"
        assert row.claim_token is not None
        container = session.get(build_model.BuildAttempt, attempt_id)
        assert container.status == "succeeded"
        assert container.current_execution_id is None


def test_mark_attempt_running_claims_queued_execution(session_factory, monkeypatch):
    monkeypatch.setenv("EXECUTION_MINTING", "1")
    with session_factory() as session:
        attempt_id, execution_id = _seed_scheduled(session)
        session.commit()

    cli._mark_attempt_running(
        attempt_id,
        "worker-1",
        session_factory=session_factory,
    )

    with session_factory() as session:
        row = session.get(exec_model.Execution, execution_id)
        assert row.status == "running"
        assert row.worker_id == "worker-1"
        assert row.claim_token is not None
        assert row.lease_expires_at is not None
        container = session.get(build_model.BuildAttempt, attempt_id)
        assert container.status == "running"
        assert container.worker == "worker-1"
        assert container.current_execution_id == execution_id
        assert container.latest_execution_id == execution_id


def test_records_failed_outcome_with_error(session_factory, monkeypatch):
    monkeypatch.setenv("EXECUTION_MINTING", "1")
    with session_factory() as session:
        attempt_id, execution_id = _seed_scheduled(session)
        session.commit()

    cli._mark_attempt_running(
        attempt_id,
        "worker-1",
        session_factory=session_factory,
    )
    cli._record_execution_outcome(
        attempt_id,
        "worker-1",
        {"processed": 1, "failed": 1, "outcomes": [{"error": "docker build failed"}]},
        session_factory=session_factory,
    )

    with session_factory() as session:
        row = session.get(exec_model.Execution, execution_id)
        assert row.status == "failed"
        assert row.error == "docker build failed"
        container = session.get(build_model.BuildAttempt, attempt_id)
        assert container.status == "failed"


def test_recorded_failed_outcome_is_not_reaped_to_lost(session_factory, monkeypatch):
    monkeypatch.setenv("EXECUTION_MINTING", "1")
    with session_factory() as session:
        attempt_id, execution_id = _seed_scheduled(session)
        session.commit()

    cli._mark_attempt_running(
        attempt_id,
        "worker-1",
        session_factory=session_factory,
    )
    cli._record_execution_outcome(
        attempt_id,
        "worker-1",
        {
            "processed": 1,
            "failed": 1,
            "outcomes": [
                {"error": "re-3ce05a7b-0013: contract_failed (implement evidence incomplete)"}
            ],
        },
        session_factory=session_factory,
    )

    with session_factory() as session:
        reaped = ExecutionsRepository(session).reap_expired(
            now=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        session.commit()

    assert reaped == []
    with session_factory() as session:
        row = session.get(exec_model.Execution, execution_id)
        container = session.get(build_model.BuildAttempt, attempt_id)
        assert row.status == "failed"
        assert row.error == "re-3ce05a7b-0013: contract_failed (implement evidence incomplete)"
        assert container.status == "failed"
        assert container.current_execution_id is None


def test_noop_when_flag_disabled(session_factory, monkeypatch):
    monkeypatch.setenv("EXECUTION_MINTING", "0")
    with session_factory() as session:
        attempt_id, execution_id = _seed_scheduled(session)
        session.commit()

    cli._record_execution_outcome(
        attempt_id, "w", {"processed": 1, "failed": 0}, session_factory=session_factory
    )

    with session_factory() as session:
        row = session.get(exec_model.Execution, execution_id)
        assert row.status == "queued"
