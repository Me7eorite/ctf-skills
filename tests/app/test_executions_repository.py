"""PostgreSQL-backed tests for the execution lease/fencing repository."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models import executions as exec_model
from persistence.models import research as research_model
from persistence.models.progress import ProgressSnapshot
from persistence.repositories import (
    BuildAttemptsRepository,
    ExecutionPersistenceError,
    ExecutionsRepository,
)
from persistence.session import SessionFactory

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres

TTL = 300


@pytest.fixture(scope="module")
def session_factory() -> SessionFactory:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
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
        session.execute(sa.delete(ProgressSnapshot))
        session.execute(sa.delete(build_model.BuildAttempt))
        session.execute(sa.delete(task_model.DesignTask))
        session.execute(sa.delete(research_model.ResearchRun))
        session.execute(sa.delete(research_model.GenerationRequest))
        session.commit()
    yield


def _seed_container(session) -> build_model.BuildAttempt:
    request = research_model.GenerationRequest(
        id=uuid4(),
        category="web",
        topic=f"topic-{uuid4()}",
        target_count=1,
        difficulty_distribution={"easy": 1},
        status="researched",
    )
    run = research_model.ResearchRun(
        id=uuid4(),
        generation_request_id=request.id,
        attempt=1,
        status="completed",
    )
    task = task_model.DesignTask(
        id=uuid4(),
        generation_request_id=request.id,
        research_run_id=run.id,
        task_no=1,
        challenge_id=f"web-{uuid4().hex[:8]}",
        title="Task",
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
        status="designed",
    )
    session.add_all([request, run, task])
    session.flush()
    attempt = BuildAttemptsRepository(session).create_attempt(task.id, "b.json")
    return session.get(build_model.BuildAttempt, attempt.id)


def test_schedule_claim_and_terminal_lifecycle(session_factory: SessionFactory):
    with session_factory() as session:
        container = _seed_container(session)
        repo = ExecutionsRepository(session)

        e1 = repo.schedule_execution(container.id, execution_kind="initial")
        assert e1.iteration_no == 1
        assert e1.status == "queued"
        assert e1.claim_token is None
        session.refresh(container)
        assert container.latest_execution_id == e1.id
        assert container.current_execution_id is None
        assert container.status == "queued"

        claimed, token = repo.claim_queued(
            container.id, worker_id="w1", lease_ttl_seconds=TTL
        )
        assert claimed.status == "claimed"
        assert claimed.claim_token == token
        session.refresh(container)
        assert container.current_execution_id == e1.id
        assert container.status == "running"
        assert container.started_at is not None

        repo.update_to_running(e1.id, claim_token=token)
        terminal = repo.update_to_terminal(
            e1.id, claim_token=token, status="succeeded"
        )
        assert terminal.status == "succeeded"
        assert terminal.finished_at is not None
        session.refresh(container)
        assert container.current_execution_id is None
        assert container.status == "succeeded"


def test_retry_appends_iteration_under_same_container(
    session_factory: SessionFactory,
):
    with session_factory() as session:
        container = _seed_container(session)
        repo = ExecutionsRepository(session)
        e1 = repo.schedule_execution(container.id, execution_kind="initial")
        _, token = repo.claim_queued(container.id, worker_id="w", lease_ttl_seconds=TTL)
        repo.update_to_terminal(e1.id, claim_token=token, status="failed")

        e2 = repo.schedule_execution(
            container.id, execution_kind="retry", parent_execution_id=e1.id
        )
        assert e2.iteration_no == 2
        # No new container row was created.
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(build_model.BuildAttempt))
            == 1
        )
        session.refresh(container)
        assert container.latest_execution_id == e2.id
        assert container.status == "queued"
        assert container.finished_at is None


def test_one_nonterminal_execution_per_container(session_factory: SessionFactory):
    with session_factory() as session:
        container = _seed_container(session)
        repo = ExecutionsRepository(session)
        repo.schedule_execution(container.id, execution_kind="initial")
        with pytest.raises((IntegrityError, ExecutionPersistenceError)):
            # second non-terminal execution must collide with the partial index
            session.add(
                exec_model.Execution(
                    id=uuid4(),
                    build_attempt_id=container.id,
                    iteration_no=99,
                    execution_kind="retry",
                    parent_execution_id=None,
                    status="queued",
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.flush()


def test_stale_token_terminal_is_rejected(session_factory: SessionFactory):
    with session_factory() as session:
        container = _seed_container(session)
        repo = ExecutionsRepository(session)
        e1 = repo.schedule_execution(container.id, execution_kind="initial")
        _, good = repo.claim_queued(container.id, worker_id="w", lease_ttl_seconds=TTL)
        with pytest.raises(ExecutionPersistenceError):
            repo.update_to_terminal(e1.id, claim_token=uuid4(), status="succeeded")
        # the good token still works
        repo.update_to_terminal(e1.id, claim_token=good, status="succeeded")


def test_reaper_marks_expired_current_lost(session_factory: SessionFactory):
    with session_factory() as session:
        container = _seed_container(session)
        repo = ExecutionsRepository(session)
        e1 = repo.schedule_execution(container.id, execution_kind="initial")
        # claim with an already-expired lease
        past = datetime.now(timezone.utc) - timedelta(seconds=TTL + 10)
        repo.claim_queued(container.id, worker_id="w", lease_ttl_seconds=TTL, now=past)

        reaped = repo.reap_expired()
        assert e1.id in reaped
        session.refresh(container)
        assert container.current_execution_id is None
        assert container.status == "lost"
        row = repo.get(e1.id)
        assert row.status == "lost"


def test_heartbeat_three_gate(session_factory: SessionFactory):
    with session_factory() as session:
        container = _seed_container(session)
        repo = ExecutionsRepository(session)
        e1 = repo.schedule_execution(container.id, execution_kind="initial")
        _, token = repo.claim_queued(container.id, worker_id="w", lease_ttl_seconds=TTL)
        before = repo.get(e1.id).lease_expires_at
        later = datetime.now(timezone.utc) + timedelta(seconds=30)
        beat = repo.heartbeat(
            e1.id, claim_token=token, lease_ttl_seconds=TTL, now=later
        )
        assert beat.lease_expires_at > before
        # after terminal, heartbeat (no longer current/active) is rejected
        repo.update_to_terminal(e1.id, claim_token=token, status="failed")
        with pytest.raises(ExecutionPersistenceError):
            repo.heartbeat(e1.id, claim_token=token, lease_ttl_seconds=TTL)
