"""Postgres-backed tests for DesignTaskPlanningService end-to-end flow."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from domain.design_task_validators import DesignTaskValidationError
from persistence.models import design_tasks as dt_model
from persistence.models import research as model
from persistence.repositories import DesignTaskRepository
from persistence.session import SessionFactory
from services import DesignTaskPlanningService, ResearchJobService
from services import design_task_planning_service as planning_module
from services.design_task_planning_service import validate_finding_provenance

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


def _seed(
    session_factory: SessionFactory,
    *,
    target_count: int = 3,
    distribution=None,
    category: str = "web",
    finished: bool = True,
):
    distribution = distribution or {"easy": 1, "medium": 2}
    service = ResearchJobService(session_factory)
    request, run = service.submit_request(
        category=category,
        topic="SQL injection",
        target_count=target_count,
        difficulty_distribution=distribution,
    )
    session = session_factory()
    try:
        for index in range(2):
            session.add(
                model.ResearchSource(
                    id=uuid4(),
                    research_run_id=run.id,
                    url=f"https://example.com/{index}",
                    title=f"Source {index}",
                    summary=f"summary {index}",
                    content_hash=f"hash-{index}",
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        for index in range(2):
            session.add(
                model.ResearchFinding(
                    id=uuid4(),
                    research_run_id=run.id,
                    kind="technique",
                    label=f"technique-{index}",
                    summary=f"summary {index}",
                )
            )
        if finished:
            run_row = session.get(model.ResearchRun, run.id)
            run_row.status = "completed"
            run_row.finished_at = datetime.now(timezone.utc)
            request_row = session.get(model.GenerationRequest, request.id)
            request_row.status = "researched"
        session.commit()
    finally:
        session.close()
    return request, run


def test_generate_creates_target_count_drafts(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=3, distribution={"easy": 1, "medium": 2})
    service = DesignTaskPlanningService(session_factory)

    tasks = service.generate_for_request(request.id)

    assert len(tasks) == 3
    assert {t.status for t in tasks} == {"draft"}
    assert sorted(t.task_no for t in tasks) == [1, 2, 3]
    assert sorted(t.difficulty for t in tasks) == ["easy", "medium", "medium"]
    assert all(t.generation_request_id == request.id for t in tasks)
    assert all(t.challenge_id.startswith("web-") for t in tasks)
    assert all(t.finding_ids for t in tasks)


def test_generate_rejected_when_no_completed_run(session_factory: SessionFactory):
    request, _ = _seed(session_factory, finished=False)
    service = DesignTaskPlanningService(session_factory)

    with pytest.raises(DesignTaskValidationError, match="completed research run"):
        service.generate_for_request(request.id)

    session = session_factory()
    try:
        rows = DesignTaskRepository(session).list_design_tasks(request.id)
        assert rows == []
    finally:
        session.close()


def test_difficulty_distribution_is_preserved(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=3, distribution={"easy": 1, "medium": 2})
    service = DesignTaskPlanningService(session_factory)
    tasks = service.generate_for_request(request.id)
    easy = [t for t in tasks if t.difficulty == "easy"]
    medium = [t for t in tasks if t.difficulty == "medium"]
    assert len(easy) == 1
    assert len(medium) == 2


def test_generate_can_replace_draft_tasks(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)
    first = service.generate_for_request(request.id)
    second = service.generate_for_request(request.id)
    assert {t.id for t in first}.isdisjoint({t.id for t in second})
    session = session_factory()
    try:
        rows = DesignTaskRepository(session).list_design_tasks(request.id)
        assert {t.id for t in rows} == {t.id for t in second}
    finally:
        session.close()


def test_generate_blocked_when_any_task_queued(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)
    initial = service.generate_for_request(request.id)

    session = session_factory()
    try:
        DesignTaskRepository(session).set_design_task_status(initial[0].id, "queued")
        session.commit()
    finally:
        session.close()

    with pytest.raises(DesignTaskValidationError, match="cannot regenerate"):
        service.generate_for_request(request.id)


def test_validate_finding_provenance_rejects_empty_finding_ids():
    allowed = {uuid4()}
    candidates = [
        {"task_no": 1, "finding_ids": []},
    ]
    with pytest.raises(DesignTaskValidationError, match="cites no finding"):
        validate_finding_provenance(
            candidates, allowed_finding_ids=allowed, research_run_id=uuid4()
        )


def test_validate_finding_provenance_rejects_foreign_finding_id():
    allowed_finding = uuid4()
    foreign_finding = uuid4()
    candidates = [
        {"task_no": 1, "finding_ids": [foreign_finding]},
    ]
    with pytest.raises(DesignTaskValidationError, match="not from research run"):
        validate_finding_provenance(
            candidates,
            allowed_finding_ids={allowed_finding},
            research_run_id=uuid4(),
        )


def test_validate_finding_provenance_accepts_subset_from_run():
    a, b = uuid4(), uuid4()
    candidates = [
        {"task_no": 1, "finding_ids": [a]},
        {"task_no": 2, "finding_ids": [str(b)]},
    ]
    validate_finding_provenance(
        candidates, allowed_finding_ids={a, b}, research_run_id=uuid4()
    )


def test_generate_rejects_planner_with_empty_finding_ids(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)

    original = planning_module._plan_candidates

    def _bad_planner(req, run, findings):
        rows = original(req, run, findings)
        for row in rows:
            row["finding_ids"] = []
        return rows

    monkeypatch.setattr(planning_module, "_plan_candidates", _bad_planner)

    with pytest.raises(DesignTaskValidationError, match="cites no finding"):
        service.generate_for_request(request.id)

    session = session_factory()
    try:
        rows = DesignTaskRepository(session).list_design_tasks(request.id)
        assert rows == []
    finally:
        session.close()


def test_generate_rejects_planner_with_foreign_finding_id(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)

    original = planning_module._plan_candidates
    foreign = uuid4()

    def _bad_planner(req, run, findings):
        rows = original(req, run, findings)
        rows[0]["finding_ids"] = [foreign]
        return rows

    monkeypatch.setattr(planning_module, "_plan_candidates", _bad_planner)

    with pytest.raises(DesignTaskValidationError, match="not from research run"):
        service.generate_for_request(request.id)

    session = session_factory()
    try:
        rows = DesignTaskRepository(session).list_design_tasks(request.id)
        assert rows == []
    finally:
        session.close()


def test_generate_replaces_archived_tasks(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)
    first = service.generate_for_request(request.id)

    session = session_factory()
    try:
        repo = DesignTaskRepository(session)
        for task in first:
            repo.set_design_task_status(task.id, "archived")
        session.commit()
    finally:
        session.close()

    second = service.generate_for_request(request.id)
    assert {t.status for t in second} == {"draft"}
    assert {t.id for t in second}.isdisjoint({t.id for t in first})
