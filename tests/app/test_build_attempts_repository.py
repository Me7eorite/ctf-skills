"""PostgreSQL-backed tests for the build-attempt repository."""

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
from persistence.models import research as research_model
from persistence.models.progress import ProgressSnapshot
from persistence.repositories import BuildAttemptsRepository
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
        subprocess.run(
            ["uv", "run", "alembic", "downgrade", "base"],
            cwd=ROOT,
            env=env,
            check=False,
        )


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory):
    with session_factory() as session:
        session.execute(sa.delete(ProgressSnapshot))
        session.execute(sa.delete(build_model.BuildAttempt))
        session.execute(sa.delete(task_model.DesignTask))
        session.execute(sa.delete(research_model.ResearchRun))
        session.execute(sa.delete(research_model.GenerationRequest))
        session.commit()
    yield


def _seed_task(
    session,
    *,
    task_no: int = 1,
    category: str = "web",
    title: str | None = None,
):
    request = research_model.GenerationRequest(
        id=uuid4(),
        category=category,
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
        task_no=task_no,
        challenge_id=f"{category}-{uuid4().hex[:8]}",
        title=title or f"Task {task_no}",
        category=category,
        difficulty="easy",
        primary_technique="test technique",
        learning_objective="test objective",
        points=100,
        port=8080 if category in {"web", "pwn"} else None,
        scenario="",
        constraints={},
        evidence_summary="",
        finding_ids=[],
        status="designed",
    )
    session.add_all([request, run, task])
    session.flush()
    return task


def test_insert_get_and_attempt_number_auto_increment(
    session_factory: SessionFactory,
):
    with session_factory() as session:
        task = _seed_task(session)
        repo = BuildAttemptsRepository(session)
        first_id = uuid4()
        first = repo.create_attempt(
            task.id,
            "attempt-1.json",
            attempt_id=first_id,
        )
        assert first.id == first_id
        assert first.attempt_no == 1
        assert first.status == "queued"
        assert first.artifact_status == "unknown"
        assert first.started_at is None
        assert first.finished_at is None

        terminal = repo.update_to_terminal(
            first.id,
            status="failed",
            error="build failed",
        )
        second = repo.create_attempt(task.id, "attempt-2.json")
        session.commit()

        assert terminal.finished_at is not None
        assert second.attempt_no == 2
        assert repo.get(second.id) == second
        assert repo.latest_for_design_task(task.id) == second
        assert [item.attempt_no for item in repo.list_for_design_task(task.id)] == [
            1,
            2,
        ]


def test_partial_unique_index_rejects_two_active_attempts(
    session_factory: SessionFactory,
):
    with session_factory() as session:
        task = _seed_task(session)
        repo = BuildAttemptsRepository(session)
        repo.create_attempt(task.id, "active-1.json")
        with pytest.raises(IntegrityError):
            repo.create_attempt(task.id, "active-2.json")
        session.rollback()


def test_running_and_terminal_updates_set_timestamps(
    session_factory: SessionFactory,
):
    with session_factory() as session:
        task = _seed_task(session)
        repo = BuildAttemptsRepository(session)
        attempt = repo.create_attempt(task.id, "running.json")
        claimed_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        running = repo.update_to_running(
            attempt.id,
            worker="hermes-01",
            started_at=claimed_at,
        )
        finished = repo.update_to_terminal(
            attempt.id,
            status="succeeded",
            resulting_challenge_dir="work/challenges/web/example",
            artifact_status="present",
        )
        missing = repo.update_artifact_status(attempt.id, "missing")
        session.commit()

        assert running.status == "running"
        assert running.started_at == claimed_at
        assert finished.status == "succeeded"
        assert finished.finished_at is not None
        assert finished.started_at == claimed_at
        assert finished.artifact_status == "present"
        assert missing.status == "succeeded"
        assert missing.artifact_status == "missing"


def test_folded_list_filters_latest_before_filtering_and_honors_limit(
    session_factory: SessionFactory,
):
    with session_factory() as session:
        repo = BuildAttemptsRepository(session)
        task_a = _seed_task(session, task_no=1, title="A")
        task_b = _seed_task(session, task_no=2, title="B")
        task_c = _seed_task(session, task_no=3, title="C")

        old_a = repo.create_attempt(task_a.id, "a-old.json")
        repo.update_to_terminal(old_a.id, status="failed")
        latest_a = repo.create_attempt(task_a.id, "a-latest.json")
        attempt_b = repo.create_attempt(task_b.id, "b.json")
        repo.update_to_terminal(attempt_b.id, status="failed")
        attempt_c = repo.create_attempt(task_c.id, "c.json")
        repo.update_to_terminal(attempt_c.id, status="failed")

        now = datetime.now(timezone.utc)
        session.get(build_model.BuildAttempt, latest_a.id).created_at = now
        session.get(build_model.BuildAttempt, attempt_b.id).created_at = now - timedelta(
            minutes=1
        )
        session.get(build_model.BuildAttempt, attempt_c.id).created_at = now - timedelta(
            minutes=2
        )
        session.flush()

        failed = repo.list_attempts(status="failed", limit=10)
        limited = repo.list_attempts(limit=2)

        assert [item.title for item in failed] == ["B", "C"]
        assert all(item.id != old_a.id for item in failed)
        assert [item.title for item in limited] == ["A", "B"]


def test_folded_list_joins_progress_snapshot_without_duplicate_rows(
    session_factory: SessionFactory,
):
    with session_factory() as session:
        task = _seed_task(session, title="Progress task")
        repo = BuildAttemptsRepository(session)
        attempt = repo.create_attempt(task.id, "progress.json")
        session.add_all(
            [
                ProgressSnapshot(
                    shard="progress.json",
                    challenge_id="",
                    worker="hermes-01",
                    stage="queued",
                    status="running",
                    percent=0,
                    message="claimed",
                ),
                ProgressSnapshot(
                    shard="progress.json",
                    challenge_id=task.challenge_id,
                    worker="hermes-01",
                    stage="build",
                    status="running",
                    percent=64,
                    message="building",
                ),
            ]
        )
        session.flush()

        rows = repo.list_attempts(design_task_id=task.id)

        assert len(rows) == 1
        assert rows[0].id == attempt.id
        assert rows[0].percent == 64
        assert rows[0].generation_request_id == task.generation_request_id
        assert rows[0].category == "web"
