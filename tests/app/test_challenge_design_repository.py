"""Postgres-backed tests for the challenge-design repository."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from persistence.models import challenge_designs as cd_model
from persistence.models import design_tasks as dt_model
from persistence.models import research as model
from persistence.repositories import (
    ChallengeDesignPersistenceError,
    ChallengeDesignRepository,
)
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
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
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
        session.execute(sa.delete(cd_model.ChallengeDesign))
        session.execute(sa.delete(cd_model.DesignAttempt))
        session.execute(sa.delete(dt_model.DesignTask))
        session.execute(sa.delete(model.ResearchFindingSource))
        session.execute(sa.delete(model.ResearchFinding))
        session.execute(sa.delete(model.ResearchSource))
        session.execute(sa.delete(model.ResearchRun))
        session.execute(sa.delete(model.GenerationRequest))
        session.commit()
    yield


def _seed_design_task(session_factory: SessionFactory, *, status: str = "queued"):
    with session_factory() as session:
        request = model.GenerationRequest(
            id=uuid4(),
            category="web",
            topic="SQL injection",
            target_count=1,
            difficulty_distribution={"easy": 1},
            runtime_constraints={},
            seed_urls=[],
            max_attempts=2,
            status="researched",
        )
        run = model.ResearchRun(
            id=uuid4(),
            generation_request_id=request.id,
            attempt=1,
            status="completed",
            finished_at=datetime.now(timezone.utc),
            profile_name_used="default",
        )
        task = dt_model.DesignTask(
            id=uuid4(),
            generation_request_id=request.id,
            research_run_id=run.id,
            task_no=1,
            challenge_id="web-0001",
            title="Blind Login",
            category="web",
            difficulty="easy",
            primary_technique="boolean blind sqli",
            learning_objective="Extract data through conditional responses.",
            points=100,
            port=8081,
            scenario="Login form leaks booleans through redirect behavior.",
            constraints={"runtime": "docker"},
            evidence_summary="Finding supports boolean inference.",
            finding_ids=[],
            status=status,
        )
        session.add_all([request, run, task])
        session.commit()
        return task.id


def _payload() -> dict[str, object]:
    return {
        "id": "web-0001",
        "title": "Blind Login",
        "category": "web",
        "difficulty": "easy",
        "points": 100,
        "flag_format": "flag{...}",
        "learning_objective": "Extract data through conditional responses.",
    }


def test_attempt_round_trip_and_prompt_path(session_factory: SessionFactory):
    task_id = _seed_design_task(session_factory)
    session = session_factory()
    try:
        repo = ChallengeDesignRepository(session)
        attempt = repo.create_attempt(task_id, 1, "alice", "default")
        assert attempt.status == "running"
        assert attempt.attempt == 1
        assert attempt.claimed_by == "alice"
        assert attempt.profile_name_used == "default"
        assert attempt.started_at is not None
        assert session.get(dt_model.DesignTask, task_id).status == "designing"

        recorded = repo.record_prompt_path(
            attempt.id,
            attempt.claim_token,
            "work/design/prompts/a.md",
        )
        assert recorded.prompt_path == "work/design/prompts/a.md"
        assert repo.get_attempt(attempt.id) == recorded
        assert repo.latest_attempt(task_id) == recorded
        assert repo.list_attempts(task_id) == [recorded]
    finally:
        session.rollback()
        session.close()


def test_token_fence_rejects_wrong_token(session_factory: SessionFactory):
    task_id = _seed_design_task(session_factory)
    session = session_factory()
    try:
        repo = ChallengeDesignRepository(session)
        attempt = repo.create_attempt(task_id, 1, "alice", "default")
        with pytest.raises(ChallengeDesignPersistenceError, match="token"):
            repo.record_prompt_path(attempt.id, uuid4(), "work/design/prompts/a.md")
    finally:
        session.rollback()
        session.close()


def test_complete_attempt_writes_design_and_marks_task_designed(
    session_factory: SessionFactory,
):
    task_id = _seed_design_task(session_factory)
    session = session_factory()
    try:
        repo = ChallengeDesignRepository(session)
        attempt = repo.create_attempt(task_id, 1, "alice", "default")
        design = repo.complete_attempt(
            attempt.id,
            attempt.claim_token,
            "work/design/logs/a.log",
            _payload(),
            "Blind Login - boolean blind sqli",
            "flag{...}",
            "quality gate passed",
            True,
        )

        assert design.design_task_id == task_id
        assert design.design_attempt_id == attempt.id
        assert design.payload["id"] == "web-0001"
        assert design.status == "draft"
        assert repo.latest_design(task_id) == design
        completed = repo.get_attempt(attempt.id)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.hermes_log_path == "work/design/logs/a.log"
        assert completed.finished_at is not None
        assert session.get(dt_model.DesignTask, task_id).status == "designed"
    finally:
        session.rollback()
        session.close()


def test_partial_unique_constraint_allows_only_one_draft(
    session_factory: SessionFactory,
):
    task_id = _seed_design_task(session_factory)
    session = session_factory()
    try:
        repo = ChallengeDesignRepository(session)
        attempt = repo.create_attempt(task_id, 1, "alice", "default")
        design = repo.complete_attempt(
            attempt.id,
            attempt.claim_token,
            "work/design/logs/a.log",
            _payload(),
            "Blind Login",
            "flag{...}",
            "quality gate passed",
            True,
        )
        session.add(
            cd_model.ChallengeDesign(
                id=uuid4(),
                design_task_id=task_id,
                design_attempt_id=design.design_attempt_id,
                payload=_payload(),
                summary="Second draft",
                flag_format="flag{...}",
                validation_notes="quality gate passed",
                quality_gate_passed=True,
                status="draft",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
    finally:
        session.rollback()
        session.close()


def test_fail_attempt_requeues_without_attempt_placeholder(
    session_factory: SessionFactory,
):
    task_id = _seed_design_task(session_factory)
    session = session_factory()
    try:
        repo = ChallengeDesignRepository(session)
        attempt = repo.create_attempt(task_id, 1, "alice", "default")
        failed = repo.fail_attempt(
            attempt.id,
            attempt.claim_token,
            "work/design/logs/a.log",
            "schema invalid",
            max_attempts=2,
        )

        assert failed.status == "failed"
        assert failed.last_error == "schema invalid"
        assert session.get(dt_model.DesignTask, task_id).status == "queued"
        attempts = repo.list_attempts(task_id)
        assert len(attempts) == 1
        assert attempts[0].attempt == 1
    finally:
        session.rollback()
        session.close()


def test_fail_attempt_exhausts_to_failed(session_factory: SessionFactory):
    task_id = _seed_design_task(session_factory)
    session = session_factory()
    try:
        repo = ChallengeDesignRepository(session)
        attempt = repo.create_attempt(task_id, 1, "alice", "default")
        failed = repo.fail_attempt(
            attempt.id,
            attempt.claim_token,
            "work/design/logs/a.log",
            "timeout",
            max_attempts=1,
        )

        assert failed.status == "failed"
        assert session.get(dt_model.DesignTask, task_id).status == "failed"
        assert repo.latest_design(task_id) is None
    finally:
        session.rollback()
        session.close()
