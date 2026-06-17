"""Postgres-backed tests for the design-task repository."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from domain.design_task_validators import DesignTaskValidationError
from persistence.models import design_tasks as dt_model
from persistence.models import research as model
from persistence.repositories import DesignTaskRepository, ResearchRepository
from persistence.session import SessionFactory
from services import ResearchJobService

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
        session.execute(sa.delete(dt_model.DesignTask))
        session.execute(sa.delete(model.ResearchFindingSource))
        session.execute(sa.delete(model.ResearchFinding))
        session.execute(sa.delete(model.ResearchSource))
        session.execute(sa.delete(model.HermesProfileBinding))
        session.execute(sa.delete(model.ResearchRun))
        session.execute(sa.delete(model.GenerationRequest))
        session.execute(
            sa.delete(model.ChallengeCategory).where(
                model.ChallengeCategory.code.not_in(["web", "pwn", "re"])
            )
        )
        session.add(
            model.HermesProfileBinding(
                role="research",
                profile_name="default",
                description="默认绑定，operator 可改",
                status="enabled",
            )
        )
        session.commit()
    yield


def _seed_completed_run(session_factory: SessionFactory, **request_overrides):
    """Create a generation request + completed research run + a finding/source."""
    service = ResearchJobService(session_factory)
    request, run = service.submit_request(
        category=request_overrides.get("category", "web"),
        topic=request_overrides.get("topic", "SQL injection"),
        target_count=request_overrides.get("target_count", 2),
        difficulty_distribution=request_overrides.get(
            "difficulty_distribution", {"easy": 1, "medium": 1}
        ),
    )
    session = session_factory()
    try:
        ResearchRepository(session)
        source_row = model.ResearchSource(
            id=uuid4(),
            research_run_id=run.id,
            url="https://example.com/sqli",
            title="SQLi primer",
            summary="bool-based blind",
            content_hash="abc",
            fetched_at=datetime.now(timezone.utc),
        )
        session.add(source_row)
        finding_row = model.ResearchFinding(
            id=uuid4(),
            research_run_id=run.id,
            kind="technique",
            label="boolean blind",
            summary="branching on truthy/falsy responses",
        )
        session.add(finding_row)
        session.flush()
        session.add(
            model.ResearchFindingSource(
                finding_id=finding_row.id,
                source_id=source_row.id,
            )
        )
        run_row = session.get(model.ResearchRun, run.id)
        run_row.status = "completed"
        run_row.finished_at = datetime.now(timezone.utc)
        request_row = session.get(model.GenerationRequest, request.id)
        request_row.status = "researched"
        session.commit()
        return request.id, run.id, finding_row.id
    finally:
        session.close()


def _candidate(task_no: int, difficulty: str, finding_id: UUID, **overrides):
    base = {
        "task_no": task_no,
        "challenge_id": f"web-{task_no:04d}",
        "title": f"Drill {task_no}",
        "category": "web",
        "difficulty": difficulty,
        "primary_technique": "boolean blind sqli",
        "learning_objective": "extract data via inference",
        "points": 100,
        "port": 8080 + task_no,
        "scenario": "Login form returns 200/302 based on row existence.",
        "constraints": {"runtime": "docker"},
        "evidence_summary": "Backed by finding boolean blind.",
        "finding_ids": [finding_id],
    }
    base.update(overrides)
    return base


def test_round_trip_create_list_get(session_factory: SessionFactory):
    request_id, run_id, finding_id = _seed_completed_run(session_factory)
    session = session_factory()
    try:
        repo = DesignTaskRepository(session)
        created = repo.replace_draft_or_archived_tasks(
            generation_request_id=request_id,
            research_run_id=run_id,
            parent_category="web",
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
            candidates=[
                _candidate(1, "easy", finding_id),
                _candidate(2, "medium", finding_id),
            ],
        )
        session.commit()

        listed = repo.list_design_tasks(request_id)
        assert [t.task_no for t in listed] == [1, 2]
        assert [t.status for t in listed] == ["draft", "draft"]
        assert [t.challenge_id for t in listed] == ["web-0001", "web-0002"]
        assert listed[0].finding_ids == (finding_id,)
        single = repo.get_design_task(created[0].id)
        assert single is not None and single.task_no == 1
    finally:
        session.close()


def test_unique_challenge_id_scoped_per_request(session_factory: SessionFactory):
    request_id_a, run_id_a, finding_id_a = _seed_completed_run(session_factory)
    request_id_b, run_id_b, finding_id_b = _seed_completed_run(
        session_factory, topic="SSRF basics"
    )
    session = session_factory()
    try:
        repo = DesignTaskRepository(session)
        repo.replace_draft_or_archived_tasks(
            generation_request_id=request_id_a,
            research_run_id=run_id_a,
            parent_category="web",
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
            candidates=[
                _candidate(1, "easy", finding_id_a),
                _candidate(2, "medium", finding_id_a),
            ],
        )
        # Same challenge_id "web-0001" must succeed for the second
        # request because uniqueness is per-(generation_request_id, challenge_id).
        repo.replace_draft_or_archived_tasks(
            generation_request_id=request_id_b,
            research_run_id=run_id_b,
            parent_category="web",
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
            candidates=[
                _candidate(1, "easy", finding_id_b),
                _candidate(2, "medium", finding_id_b),
            ],
        )
        session.commit()
        assert len(repo.list_design_tasks(request_id_a)) == 2
        assert len(repo.list_design_tasks(request_id_b)) == 2
    finally:
        session.close()


def test_status_transition_only_allows_planning_paths(session_factory: SessionFactory):
    request_id, run_id, finding_id = _seed_completed_run(session_factory)
    session = session_factory()
    try:
        repo = DesignTaskRepository(session)
        created = repo.replace_draft_or_archived_tasks(
            generation_request_id=request_id,
            research_run_id=run_id,
            parent_category="web",
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
            candidates=[
                _candidate(1, "easy", finding_id),
                _candidate(2, "medium", finding_id),
            ],
        )
        first_id = created[0].id
        queued = repo.set_design_task_status(first_id, "queued")
        assert queued.status == "queued"
        archived = repo.set_design_task_status(first_id, "archived")
        assert archived.status == "archived"
        with pytest.raises(DesignTaskValidationError, match="not allowed"):
            repo.set_design_task_status(created[1].id, "designing")
    finally:
        session.rollback()
        session.close()


def test_regeneration_blocked_when_any_task_queued(session_factory: SessionFactory):
    request_id, run_id, finding_id = _seed_completed_run(session_factory)
    session = session_factory()
    try:
        repo = DesignTaskRepository(session)
        created = repo.replace_draft_or_archived_tasks(
            generation_request_id=request_id,
            research_run_id=run_id,
            parent_category="web",
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
            candidates=[
                _candidate(1, "easy", finding_id),
                _candidate(2, "medium", finding_id),
            ],
        )
        repo.set_design_task_status(created[0].id, "queued")
        session.commit()
        with pytest.raises(DesignTaskValidationError, match="cannot regenerate"):
            repo.replace_draft_or_archived_tasks(
                generation_request_id=request_id,
                research_run_id=run_id,
                parent_category="web",
                target_count=2,
                difficulty_distribution={"easy": 1, "medium": 1},
                candidates=[
                    _candidate(1, "easy", finding_id, title="Updated 1"),
                    _candidate(2, "medium", finding_id, title="Updated 2"),
                ],
            )
        session.rollback()
        # Existing rows untouched after rollback.
        remaining = repo.list_design_tasks(request_id)
        assert sorted(t.status for t in remaining) == ["draft", "queued"]
    finally:
        session.close()


def test_regeneration_replaces_archived_rows(session_factory: SessionFactory):
    request_id, run_id, finding_id = _seed_completed_run(session_factory)
    session = session_factory()
    try:
        repo = DesignTaskRepository(session)
        created = repo.replace_draft_or_archived_tasks(
            generation_request_id=request_id,
            research_run_id=run_id,
            parent_category="web",
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
            candidates=[
                _candidate(1, "easy", finding_id),
                _candidate(2, "medium", finding_id),
            ],
        )
        for task in created:
            repo.set_design_task_status(task.id, "archived")
        session.commit()

        regenerated = repo.replace_draft_or_archived_tasks(
            generation_request_id=request_id,
            research_run_id=run_id,
            parent_category="web",
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
            candidates=[
                _candidate(1, "easy", finding_id, title="Fresh 1"),
                _candidate(2, "medium", finding_id, title="Fresh 2"),
            ],
        )
        session.commit()
        listed = repo.list_design_tasks(request_id)
        assert [t.status for t in listed] == ["draft", "draft"]
        assert [t.title for t in listed] == ["Fresh 1", "Fresh 2"]
        assert {t.id for t in regenerated} == {t.id for t in listed}
        assert all(t.id not in {old.id for old in created} for t in regenerated)
    finally:
        session.close()


def test_replace_handles_unordered_candidates(session_factory: SessionFactory):
    # Repository sorts candidates by ``task_no`` before per-candidate
    # validation, so a planner that emits rows out of order still passes.
    request_id, run_id, finding_id = _seed_completed_run(session_factory)
    session = session_factory()
    try:
        repo = DesignTaskRepository(session)
        created = repo.replace_draft_or_archived_tasks(
            generation_request_id=request_id,
            research_run_id=run_id,
            parent_category="web",
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
            candidates=[
                _candidate(2, "medium", finding_id),
                _candidate(1, "easy", finding_id),
            ],
        )
        session.commit()
        assert sorted(t.task_no for t in created) == [1, 2]
        listed = repo.list_design_tasks(request_id)
        assert [t.task_no for t in listed] == [1, 2]
        assert [t.difficulty for t in listed] == ["easy", "medium"]
    finally:
        session.close()


def test_check_constraint_rejects_zero_points(session_factory: SessionFactory):
    request_id, run_id, _ = _seed_completed_run(session_factory)
    session = session_factory()
    try:
        row = dt_model.DesignTask(
            id=uuid4(),
            generation_request_id=request_id,
            research_run_id=run_id,
            task_no=1,
            challenge_id="web-0001",
            title="zero",
            category="web",
            difficulty="easy",
            primary_technique="x",
            learning_objective="y",
            points=0,
            port=8080,
            scenario="",
            constraints={},
            evidence_summary="",
            finding_ids=[],
            status="draft",
        )
        session.add(row)
        with pytest.raises(IntegrityError):
            session.flush()
    finally:
        session.rollback()
        session.close()
