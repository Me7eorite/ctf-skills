"""PostgreSQL-backed HTTP tests for build-attempt endpoints."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from core.paths import ProjectPaths
from persistence.models import build_attempts as build_model
from persistence.models import challenge_designs as design_model
from persistence.models import design_tasks as task_model
from persistence.models import research as research_model
from persistence.models.progress import ProgressEvent, ProgressSnapshot
from persistence.repositories import BuildAttemptsRepository
from persistence.session import SessionFactory, transaction
from web import build_attempts_endpoints
from web.dashboard import DashboardService
from web.server import create_app

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
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield SessionFactory(engine)
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        engine.dispose()
        subprocess.run(
            ["uv", "run", "alembic", "downgrade", "base"],
            cwd=ROOT,
            env=env,
            check=False,
        )


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory):
    _clean_database(session_factory)
    yield
    _clean_database(session_factory)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    service = DashboardService(paths)
    with TestClient(create_app(service)) as test_client:
        yield test_client


def _clean_database(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        session.execute(sa.delete(ProgressSnapshot))
        session.execute(sa.delete(ProgressEvent))
        session.execute(sa.delete(build_model.BuildAttempt))
        session.execute(sa.delete(design_model.ChallengeDesign))
        session.execute(sa.delete(design_model.DesignAttempt))
        session.execute(sa.delete(task_model.DesignTask))
        session.execute(sa.delete(research_model.ResearchRun))
        session.execute(sa.delete(research_model.GenerationRequest))
        session.commit()


def _seed_designed_task(
    session_factory: SessionFactory,
    *,
    task_no: int = 1,
    request_id: UUID | None = None,
    category: str = "web",
    status: str = "designed",
) -> UUID:
    with session_factory() as session:
        request = research_model.GenerationRequest(
            id=request_id or uuid4(),
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
            title=f"Task {task_no}",
            category=category,
            difficulty="easy",
            primary_technique="boolean blind sqli",
            learning_objective="Extract data through boolean responses.",
            points=100,
            port=8080 + task_no,
            scenario="Distinct login response behavior.",
            constraints={},
            evidence_summary="",
            finding_ids=[],
            status=status,
        )
        design_attempt = design_model.DesignAttempt(
            id=uuid4(),
            design_task_id=task.id,
            attempt=1,
            status="completed",
            claim_token=uuid4(),
            finished_at=datetime.now(timezone.utc),
            profile_name_used="default",
        )
        design = design_model.ChallengeDesign(
            id=uuid4(),
            design_task_id=task.id,
            design_attempt_id=design_attempt.id,
            payload={
                "event": {"flag_format": "flag{...}"},
                "challenges": [
                    {
                        "id": task.challenge_id,
                        "category": category,
                        "deployment": "docker",
                        "implementation_plan": {"runtime": "python:3.11-slim"},
                    }
                ],
            },
            summary="validated design",
            flag_format="flag{...}",
            validation_notes="passed",
            quality_gate_passed=True,
            status="draft",
        )
        session.add_all([request, run, task, design_attempt, design])
        session.commit()
        return task.id


def test_batch_submit_returns_ordered_ids(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_a = _seed_designed_task(session_factory, task_no=1)
    task_b = _seed_designed_task(session_factory, task_no=2)

    response = client.post(
        "/api/design-tasks/build",
        json={"design_task_ids": [str(task_b), str(task_a)]},
    )

    assert response.status_code == 201
    ids = [UUID(item) for item in response.json()["build_attempt_ids"]]
    assert len(ids) == 2
    with session_factory() as session:
        attempts = [BuildAttemptsRepository(session).get(item) for item in ids]
        assert [item.design_task_id for item in attempts if item] == [task_b, task_a]


def test_single_submit_conflicts_on_ineligible_or_active_task(
    client: TestClient,
    session_factory: SessionFactory,
):
    ineligible = _seed_designed_task(session_factory, status="building")
    response = client.post(f"/api/design-tasks/{ineligible}/build")
    assert response.status_code == 409
    assert "expected designed" in response.json()["detail"]

    active = _seed_designed_task(session_factory, task_no=2)
    with transaction(factory=session_factory) as session:
        BuildAttemptsRepository(session).create_attempt(active, f"{uuid4()}.json")
    response = client.post(f"/api/design-tasks/{active}/build")
    assert response.status_code == 409


def test_list_is_folded_before_status_filter_and_caps_limit(
    client: TestClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(build_attempts_endpoints, "BUILD_ATTEMPTS_LIST_MAX_LIMIT", 1)
    task_a = _seed_designed_task(session_factory, task_no=1)
    task_b = _seed_designed_task(session_factory, task_no=2)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        first_a = repo.create_attempt(task_a, f"{uuid4()}.json")
        repo.update_to_terminal(first_a.id, status="failed", error="old failure")
        latest_a = repo.create_attempt(task_a, f"{uuid4()}.json")
        repo.create_attempt(task_b, f"{uuid4()}.json")
        session.add(
            ProgressSnapshot(
                shard=latest_a.shard_basename,
                challenge_id="",
                worker="worker-1",
                stage="build",
                status="running",
                percent=60,
                message="building",
            )
        )

    failed = client.get("/api/build-attempts?status=failed")
    assert failed.status_code == 200
    assert failed.json() == []

    capped = client.get("/api/build-attempts?limit=10000")
    assert capped.status_code == 200
    assert capped.headers["X-Limit-Capped"] == "1"
    assert len(capped.json()) == 1


def test_detail_exposes_siblings_and_progress_events(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        first = repo.create_attempt(task_id, f"{uuid4()}.json")
        repo.update_to_terminal(first.id, status="failed", error="failed")
        second = repo.create_attempt(task_id, f"{uuid4()}.json")
        session.add_all(
            [
                ProgressEvent(
                    shard=first.shard_basename,
                    challenge_id="",
                    worker="worker-1",
                    stage="queued",
                    status="running",
                    percent=0,
                    message="claimed",
                ),
                ProgressEvent(
                    shard=first.shard_basename,
                    challenge_id="web-0001",
                    worker="worker-1",
                    stage="design",
                    status="passed",
                    percent=20,
                    message="carry-forward: skipping design",
                ),
            ]
        )

    response = client.get(f"/api/build-attempts/{first.id}")

    assert response.status_code == 200
    payload = response.json()
    assert [item["attempt_no"] for item in payload["sibling_attempts"]] == [1, 2]
    assert payload["sibling_attempts"][1]["id"] == str(second.id)
    assert any(
        event["message"].startswith("carry-forward:")
        for event in payload["progress_events"]
    )


def test_retry_rejects_stale_sibling(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        first = repo.create_attempt(task_id, f"{uuid4()}.json")
        repo.update_to_terminal(first.id, status="failed", error="failed")
        second = repo.create_attempt(task_id, f"{uuid4()}.json")
        repo.update_to_terminal(second.id, status="failed", error="failed again")
        session.get(task_model.DesignTask, task_id).status = "build_failed"

    response = client.post(f"/api/build-attempts/{first.id}/retry")

    assert response.status_code == 409
    assert "latest" in response.json()["detail"]


def test_validation_errors_return_400(client: TestClient):
    assert client.post("/api/design-tasks/not-a-uuid/build").status_code == 400
    assert (
        client.post("/api/design-tasks/build", json={"design_task_ids": ["nope"]})
        .status_code
        == 400
    )
    assert client.get("/api/build-attempts?status=bogus").status_code == 400
    assert client.get("/api/build-attempts?category=crypto").status_code == 400
    assert client.get("/api/build-attempts?limit=zero").status_code == 400
    assert client.get("/api/build-attempts?design_task_id=nope").status_code == 400
