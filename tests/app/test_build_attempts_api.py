"""PostgreSQL-backed HTTP tests for build-attempt endpoints."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from core.jsonio import write_json
from core.paths import ProjectPaths
from persistence.models import build_attempts as build_model
from persistence.models import challenge_designs as design_model
from persistence.models import design_tasks as task_model
from persistence.models import executions as exec_model
from persistence.models import research as research_model
from persistence.models.progress import ProgressEvent, ProgressSnapshot
from persistence.repositories import BuildAttemptsRepository, ExecutionsRepository
from persistence.session import SessionFactory, transaction
from web import build_attempts_endpoints
from web.dashboard import DashboardService
from web.server import create_app

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


class _StubBuildTaskManager:
    def __init__(self, response: tuple[bool, str] = (True, "worker started")):
        self.response = response
        self.calls: list[tuple[str, UUID]] = []
        self.lane_batches: list[list[UUID]] = []
        self.pool: dict | None = None
        self.finished_records: list[dict] = []

    def start_worker(self, *, category: str, build_attempt_id: UUID):
        self.calls.append((category, build_attempt_id))
        return self.response

    def start_sequential_worker(self, *, build_attempt_ids: list[UUID]):
        self.calls.extend(("sequence", attempt_id) for attempt_id in build_attempt_ids)
        return self.response

    def start_sequential_lanes(self, *, lanes: list[list[UUID]]):
        ok, message = self.response
        if not ok:
            return False, message, {}
        self.lane_batches = lanes
        pool_lanes = []
        for index, attempt_ids in enumerate(lanes, start=1):
            worker = f"stub-lane-{index:02d}"
            self.calls.extend((worker, attempt_id) for attempt_id in attempt_ids)
            pool_lanes.append(
                {
                    "lane": index,
                    "worker": worker,
                    "build_attempt_ids": [str(attempt_id) for attempt_id in attempt_ids],
                    "queue_length": len(attempt_ids),
                    "running": True,
                    "returncode": None,
                    "log": f"stub-lane-{index:02d}.log",
                    "message": f"{worker} running",
                }
            )
        self.pool = {
            "id": "stub-pool",
            "started_at": "2026-01-01 00:00:00",
            "running": True,
            "lane_count": len(pool_lanes),
            "active_lanes": len(pool_lanes),
            "total_attempts": sum(len(batch) for batch in lanes),
            "succeeded_lanes": 0,
            "failed_lanes": 0,
            "lanes": pool_lanes,
        }
        return True, message, self.pool

    def lane_pools_state(self):
        return [self.pool] if self.pool else []

    def stop(self):
        self.calls.append(("stop", uuid4()))
        return self.response

    def state(self):
        return {"running": False, "message": self.response[1]}

    def finished_build_workers(self):
        return self.finished_records


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
                        "primary_technique": "boolean blind sqli",
                        "techniques": ["boolean blind sqli"],
                        "intended_path": ["Infer the flag through boolean response differences."],
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


def _write_pending_attempt(
    client: TestClient,
    attempt,
    *,
    category: str = "web",
    design_task_id: UUID | None = None,
) -> Path:
    path = client.app.state.project_paths.shards / "pending" / attempt.shard_basename
    write_json(
        path,
        {
            "build_attempt_id": str(attempt.id),
            "design_task_id": str(design_task_id or attempt.design_task_id),
            "challenges": [{"id": f"{category}-0001", "category": category}],
        },
    )
    return path


def _create_canonical_attempt(repo: BuildAttemptsRepository, task_id: UUID):
    attempt_id = uuid4()
    return repo.create_attempt(
        task_id,
        f"{attempt_id}.json",
        attempt_id=attempt_id,
    )


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


def test_missing_profile_blocks_build_submission_before_enqueue(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory, category="pwn")
    client.app.state.build_profile_readiness = {
        "ready": False,
        "categories": {
            "pwn": {
                "ready": False,
                "profile": "cf-pwn",
                "create_command": "hermes profile create cf-pwn",
            }
        },
        "missing_profiles": ["cf-pwn"],
    }

    response = client.post(f"/api/design-tasks/{task_id}/build")

    assert response.status_code == 503
    assert "cf-pwn" in response.json()["detail"]
    assert "hermes profile create cf-pwn" in response.json()["detail"]
    with transaction(factory=session_factory) as session:
        assert BuildAttemptsRepository(session).latest_for_design_task(task_id) is None


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


def test_list_backfills_unknown_artifact_status_from_existing_output(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        task = session.get(task_model.DesignTask, task_id)
        repo = BuildAttemptsRepository(session)
        attempt = repo.create_attempt(task_id, f"{uuid4()}.json")
        repo.update_to_terminal(attempt.id, status="succeeded")
        challenge_id = task.challenge_id

    challenge_dir = (
        client.app.state.project_paths.challenges
        / "web"
        / f"{challenge_id}-demo"
    )
    challenge_dir.mkdir(parents=True)
    write_json(
        challenge_dir / "metadata.json",
        {
            "id": challenge_id,
            "category": "web",
            "solve_status": "passed",
            "validation_status": "passed",
        },
    )

    response = client.get("/api/build-attempts")

    assert response.status_code == 200
    [row] = response.json()
    assert row["artifact_status"] == "present"
    assert row["solve_status"] == "passed"
    assert row["validation_status"] == "passed"
    assert row["resulting_challenge_dir"].endswith(f"{challenge_id}-demo")
    with transaction(factory=session_factory) as session:
        stored = session.get(build_model.BuildAttempt, UUID(row["id"]))
        assert stored.artifact_status == "present"
        assert stored.resulting_challenge_dir.endswith(f"{challenge_id}-demo")


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
    assert payload["task_no"] == 1
    assert payload["title"] == "Task 1"
    assert any(event["message"].startswith("carry-forward:") for event in payload["progress_events"])


def test_list_and_detail_expose_latest_validation_failure_context(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory, category="pwn")
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        attempt = repo.create_attempt(task_id, f"{uuid4()}.json")
        repo.update_to_terminal(attempt.id, status="failed", error="shard execution failed")

    state_dir = (
        client.app.state.project_paths.executions
        / str(attempt.id)
        / "current"
        / "state"
    )
    state_dir.mkdir(parents=True)
    write_json(
        state_dir / "validation-history.json",
        [
            {
                "round": 1,
                "results": [
                    {
                        "challenge_id": "pwn-0001",
                        "solve_status": "failed",
                        "validation_status": "nonzero_exit",
                        "validation_failure_details": [
                            {
                                "phase": "validate",
                                "code": "pwn_prompt_eof",
                                "message": "EOF waiting for Choice:",
                                "path": "validate.sh",
                            }
                        ],
                        "validation_stderr_tail": "EOFError while waiting for Choice:",
                    }
                ],
            }
        ],
    )

    list_response = client.get("/api/build-attempts")
    assert list_response.status_code == 200
    [row] = list_response.json()
    assert row["validation_failure_class"] == "service-readiness"
    assert row["validation_failure_details"][0]["code"] == "pwn_prompt_eof"
    assert "pwn_prompt_eof" in row["validation_failure_signature"]
    assert row["validation_failure_source"] == "validation-history"
    assert row["validation_failure_round"] == 1

    detail_response = client.get(f"/api/build-attempts/{attempt.id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["validation_failure_class"] == "service-readiness"
    assert detail["validation_stderr_tail"] == "EOFError while waiting for Choice:"


def test_detail_exposes_execution_iterations(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    service = build_attempts_endpoints.BuildOrchestrationService(
        paths=client.app.state.project_paths
    )
    attempt_id = service.submit_single(task_id)
    with transaction(factory=session_factory) as session:
        repo = ExecutionsRepository(session)
        [initial] = repo.list_for_attempt(attempt_id)
        _, token = repo.claim_queued(
            attempt_id,
            worker_id="worker-01",
            lease_ttl_seconds=300,
        )
        repo.update_to_terminal(initial.id, claim_token=token, status="failed")
        session.get(task_model.DesignTask, task_id).status = "build_failed"
    service.retry(attempt_id)

    response = client.get(f"/api/build-attempts/{attempt_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["shard_basename"] == f"{attempt_id}.iter-002.json"
    assert payload["execution_count"] == 2
    assert payload["latest_execution_iteration"] == 2
    assert [item["iteration_no"] for item in payload["executions"]] == [1, 2]
    assert payload["executions"][1]["execution_kind"] == "retry"
    assert payload["executions"][1]["status"] == "queued"


def test_detail_exposes_current_workspace_timeout(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    service = build_attempts_endpoints.BuildOrchestrationService(
        paths=client.app.state.project_paths
    )
    attempt_id = service.submit_single(task_id)
    manifest = (
        client.app.state.project_paths.executions
        / str(attempt_id)
        / "current"
        / "input"
        / "manifest.json"
    )
    manifest.parent.mkdir(parents=True)
    write_json(
        manifest,
        {
            "effective_timeout_seconds": 4321,
            "timeout_source": "fixture",
        },
    )

    response = client.get(f"/api/build-attempts/{attempt_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["effective_timeout_seconds"] == 4321
    assert payload["timeout_source"] == "fixture"


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


def test_retry_response_identifies_new_execution_iteration(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    service = build_attempts_endpoints.BuildOrchestrationService(
        paths=client.app.state.project_paths
    )
    attempt_id = service.submit_single(task_id)
    with transaction(factory=session_factory) as session:
        repo = ExecutionsRepository(session)
        [initial] = repo.list_for_attempt(attempt_id)
        _, token = repo.claim_queued(
            attempt_id,
            worker_id="worker-01",
            lease_ttl_seconds=300,
        )
        repo.update_to_terminal(initial.id, claim_token=token, status="failed")
        session.get(task_model.DesignTask, task_id).status = "build_failed"

    response = client.post(f"/api/build-attempts/{attempt_id}/retry")

    assert response.status_code == 201
    payload = response.json()
    assert payload["build_attempt_id"] == str(attempt_id)
    assert payload["shard_basename"] == f"{attempt_id}.iter-002.json"
    assert payload["iteration_no"] == 2
    assert payload["execution_status"] == "queued"
    assert payload["execution_kind"] == "retry"
    assert UUID(payload["execution_id"])


def test_repair_endpoint_runs_attempt_scoped_ai_repair(
    client: TestClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("EXECUTION_MINTING", raising=False)
    task_id = _seed_designed_task(session_factory)
    service = build_attempts_endpoints.BuildOrchestrationService(
        paths=client.app.state.project_paths
    )
    attempt_id = service.submit_single(task_id)
    with transaction(factory=session_factory) as session:
        repo = ExecutionsRepository(session)
        [initial] = repo.list_for_attempt(attempt_id)
        _, token = repo.claim_queued(
            attempt_id,
            worker_id="worker-01",
            lease_ttl_seconds=300,
        )
        repo.update_to_terminal(
            initial.id,
            claim_token=token,
            status="failed",
            error="contract_failed: solver references metadata.json",
        )
        session.get(task_model.DesignTask, task_id).status = "build_failed"

    class FakeRepairService:
        def __init__(self, **_kwargs):
            pass

        def repair(self, value):
            return SimpleNamespace(
                attempt_id=value,
                repair_id="repair-fixture",
                status="succeeded",
                verification_status="passed",
                log_path="work/executions/attempt/repairs/repair-fixture/hermes.log",
                events_path="work/executions/attempt/repairs/repair-fixture/repair-events.jsonl",
                failure_summary=None,
            )

    monkeypatch.setattr(build_attempts_endpoints, "BuildAttemptRepairService", FakeRepairService)

    response = client.post(f"/api/build-attempts/{attempt_id}/repair")

    assert response.status_code == 200
    assert response.json()["build_attempt_id"] == str(attempt_id)
    assert response.json()["repair_id"] == "repair-fixture"
    assert response.json()["verification_status"] == "passed"
    payload_path = client.app.state.project_paths.shards / "pending" / f"{attempt_id}.iter-002.json"
    assert not payload_path.exists()


def test_clean_rebuild_requires_confirmation_and_replays_idempotently(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        source = repo.create_attempt(task_id, f"{uuid4()}.json")
        repo.update_to_terminal(source.id, status="failed", error="failed")
        session.get(task_model.DesignTask, task_id).status = "build_failed"

    key = str(uuid4())
    missing_confirmation = client.post(
        f"/api/build-attempts/{source.id}/clean-rebuild",
        json={"idempotency_key": key},
    )
    assert missing_confirmation.status_code == 409
    assert missing_confirmation.json()["detail"]["code"] == "confirmation_required"

    first = client.post(
        f"/api/build-attempts/{source.id}/clean-rebuild",
        json={"confirmed": True, "idempotency_key": key},
    )
    replay = client.post(
        f"/api/build-attempts/{source.id}/clean-rebuild",
        json={"confirmed": True, "idempotency_key": key},
    )
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["build_attempt_id"] == first.json()["build_attempt_id"]


def test_revalidate_endpoint_rejects_non_failed_attempt(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)

    response = client.post(f"/api/build-attempts/{attempt.id}/revalidate")

    assert response.status_code == 409
    assert "expected failed" in response.json()["detail"]


def test_revalidate_endpoint_returns_404_for_missing_attempt(client: TestClient):
    response = client.post(f"/api/build-attempts/{uuid4()}/revalidate")

    assert response.status_code == 404


def test_validation_errors_return_400(client: TestClient):
    assert client.post("/api/design-tasks/not-a-uuid/build").status_code == 400
    assert client.post("/api/design-tasks/build", json={"design_task_ids": ["nope"]}).status_code == 400
    assert client.get("/api/build-attempts?status=bogus").status_code == 400
    assert client.get("/api/build-attempts?category=crypto").status_code == 400
    assert client.get("/api/build-attempts?limit=zero").status_code == 400
    assert client.get("/api/build-attempts?design_task_id=nope").status_code == 400


def test_category_worker_starts_first_eligible_db_attempt(
    client: TestClient,
    session_factory: SessionFactory,
):
    first_task = _seed_designed_task(session_factory, task_no=1)
    second_task = _seed_designed_task(session_factory, task_no=2)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        first = _create_canonical_attempt(repo, first_task)
        second = _create_canonical_attempt(repo, second_task)
        first_row = session.get(build_model.BuildAttempt, first.id)
        second_row = session.get(build_model.BuildAttempt, second.id)
        first_row.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second_row.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    _write_pending_attempt(client, first)
    _write_pending_attempt(client, second)
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks

    response = client.post(
        "/api/build-attempts/worker/start",
        json={"category": "web"},
    )

    assert response.status_code == 202
    assert response.json()["build_attempt_id"] == str(first.id)
    assert response.json()["effective_timeout_seconds"] == 2700
    assert response.json()["timeout_source"] == "shard_policy"
    assert tasks.calls == [("web", first.id)]


def test_sequential_worker_preserves_requested_attempt_order(
    client: TestClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    first_task = _seed_designed_task(session_factory, task_no=1)
    second_task = _seed_designed_task(session_factory, task_no=2)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        first = _create_canonical_attempt(repo, first_task)
        second = _create_canonical_attempt(repo, second_task)

    _write_pending_attempt(client, first)
    _write_pending_attempt(client, second)
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks
    monkeypatch.setattr(
        build_attempts_endpoints,
        "hermes_profile_health",
        lambda _profile: (True, "", "ok"),
    )

    response = client.post(
        "/api/build-attempts/worker/start-sequential",
        json={"build_attempt_ids": [str(second.id), str(first.id)]},
    )

    assert response.status_code == 202
    assert response.json()["build_attempt_ids"] == [str(second.id), str(first.id)]
    assert response.json()["queue_length"] == 2
    assert tasks.calls == [("sequence", second.id), ("sequence", first.id)]
    with session_factory() as session:
        first_row = session.get(build_model.BuildAttempt, first.id)
        second_row = session.get(build_model.BuildAttempt, second.id)
        assert first_row.status == "queued"
        assert second_row.status == "queued"
        assert first_row.worker == "dashboard-sequential-01"
        assert second_row.worker == "dashboard-sequential-01"


def test_sequential_lane_pool_splits_selected_attempts_round_robin(
    client: TestClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    first_task = _seed_designed_task(session_factory, task_no=1)
    second_task = _seed_designed_task(session_factory, task_no=2)
    third_task = _seed_designed_task(session_factory, task_no=3)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        first = _create_canonical_attempt(repo, first_task)
        second = _create_canonical_attempt(repo, second_task)
        third = _create_canonical_attempt(repo, third_task)

    _write_pending_attempt(client, first)
    _write_pending_attempt(client, second)
    _write_pending_attempt(client, third)
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks
    monkeypatch.setattr(
        build_attempts_endpoints,
        "hermes_profile_health",
        lambda _profile: (True, "", "ok"),
    )

    response = client.post(
        "/api/build-attempts/worker/start-sequential-lanes",
        json={"build_attempt_ids": [str(first.id), str(second.id), str(third.id)], "lanes": 2},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["queue_length"] == 3
    assert body["requested_lanes"] == 2
    assert body["lane_count"] == 2
    assert body["pool"]["lanes"][0]["build_attempt_ids"] == [str(first.id), str(third.id)]
    assert body["pool"]["lanes"][1]["build_attempt_ids"] == [str(second.id)]
    assert tasks.lane_batches == [[first.id, third.id], [second.id]]
    assert tasks.calls == [
        ("stub-lane-01", first.id),
        ("stub-lane-01", third.id),
        ("stub-lane-02", second.id),
    ]
    with session_factory() as session:
        first_row = session.get(build_model.BuildAttempt, first.id)
        second_row = session.get(build_model.BuildAttempt, second.id)
        third_row = session.get(build_model.BuildAttempt, third.id)
        assert first_row.worker == "stub-lane-01"
        assert second_row.worker == "stub-lane-02"
        assert third_row.worker == "stub-lane-01"

    pools_response = client.get("/api/build-attempts/worker/pools")
    assert pools_response.status_code == 200
    assert pools_response.json()["pools"][0]["id"] == "stub-pool"


def test_retry_sequential_lane_pool_retries_failed_attempts_before_start(
    client: TestClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    first_task = _seed_designed_task(session_factory, task_no=1)
    second_task = _seed_designed_task(session_factory, task_no=2)
    third_task = _seed_designed_task(session_factory, task_no=3)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        first = _create_canonical_attempt(repo, first_task)
        second = _create_canonical_attempt(repo, second_task)
        third = _create_canonical_attempt(repo, third_task)
        repo.finalize_attempt(first.id, status="failed", error="first failed")
        repo.finalize_attempt(second.id, status="failed", error="second failed")
        repo.finalize_attempt(third.id, status="lost", error="third lost")

    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks
    monkeypatch.setattr(
        build_attempts_endpoints,
        "hermes_profile_health",
        lambda _profile: (True, "", "ok"),
    )

    response = client.post(
        "/api/build-attempts/worker/retry-sequential-lanes",
        json={"build_attempt_ids": [str(first.id), str(second.id), str(third.id)], "lanes": 2},
    )

    assert response.status_code == 202
    body = response.json()
    retry_ids = [UUID(value) for value in body["build_attempt_ids"]]
    assert body["source_build_attempt_ids"] == [str(first.id), str(second.id), str(third.id)]
    assert body["queue_length"] == 3
    assert body["requested_lanes"] == 2
    assert body["lane_count"] == 2
    assert tasks.lane_batches == [[retry_ids[0], retry_ids[2]], [retry_ids[1]]]
    with session_factory() as session:
        rows = [session.get(build_model.BuildAttempt, retry_id) for retry_id in retry_ids]
        assert [row.status for row in rows] == ["queued", "queued", "queued"]
        assert [row.worker for row in rows] == ["stub-lane-01", "stub-lane-02", "stub-lane-01"]
    for retry_id in retry_ids:
        assert (client.app.state.project_paths.shards / "pending" / f"{retry_id}.json").is_file()


def test_sequential_lane_pool_rejects_default_over_cap(client: TestClient):
    response = client.post(
        "/api/build-attempts/worker/start-sequential-lanes",
        json={
            "build_attempt_ids": ["11111111-1111-1111-1111-111111111111"],
            "lanes": 7,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "lanes must be <= 6"


def test_queue_start_runs_all_eligible_attempts_in_created_order(
    client: TestClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    first_task = _seed_designed_task(session_factory, task_no=1)
    second_task = _seed_designed_task(session_factory, task_no=2)
    third_task = _seed_designed_task(session_factory, task_no=3, category="pwn")
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        first = _create_canonical_attempt(repo, first_task)
        second = _create_canonical_attempt(repo, second_task)
        third = _create_canonical_attempt(repo, third_task)
        session.get(build_model.BuildAttempt, first.id).created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        session.get(build_model.BuildAttempt, second.id).created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        session.get(build_model.BuildAttempt, third.id).created_at = datetime(2026, 1, 3, tzinfo=timezone.utc)

    _write_pending_attempt(client, first)
    _write_pending_attempt(client, second)
    _write_pending_attempt(client, third, category="pwn")
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks
    monkeypatch.setattr(
        build_attempts_endpoints,
        "hermes_profile_health",
        lambda _profile: (True, "", "ok"),
    )

    response = client.post(
        "/api/build-attempts/queue/start",
        json={"category": "web"},
    )

    assert response.status_code == 202
    assert response.json()["build_attempt_ids"] == [str(first.id), str(second.id)]
    assert response.json()["queue_length"] == 2
    assert tasks.calls == [("sequence", first.id), ("sequence", second.id)]


def test_sequential_worker_preflight_failure_returns_409(
    client: TestClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)

    _write_pending_attempt(client, attempt)
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks
    monkeypatch.setattr(
        build_attempts_endpoints,
        "hermes_profile_health",
        lambda profile: (False, "hermes_profile_missing", f"{profile} missing"),
    )

    response = client.post(
        "/api/build-attempts/worker/start-sequential",
        json={"build_attempt_ids": [str(attempt.id)]},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "hermes_profile_missing"
    assert body["errors"] == [
        {
            "profile": "cf-web",
            "error_code": "hermes_profile_missing",
            "message": "cf-web missing",
        }
    ]
    assert tasks.calls == []


def test_queue_start_preflight_accumulates_distinct_category_errors(
    client: TestClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    web_task = _seed_designed_task(session_factory, task_no=1, category="web")
    pwn_task = _seed_designed_task(session_factory, task_no=2, category="pwn")
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        web_attempt = _create_canonical_attempt(repo, web_task)
        pwn_attempt = _create_canonical_attempt(repo, pwn_task)

    _write_pending_attempt(client, web_attempt, category="web")
    _write_pending_attempt(client, pwn_attempt, category="pwn")
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks

    def fake_health(profile: str):
        if profile == "cf-pwn":
            return False, "hermes_profile_key_missing", "cf-pwn missing key"
        return True, "", "ok"

    monkeypatch.setattr(build_attempts_endpoints, "hermes_profile_health", fake_health)

    response = client.post("/api/build-attempts/queue/start", json={})

    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "hermes_profile_key_missing"
    assert body["errors"] == [
        {
            "profile": "cf-pwn",
            "error_code": "hermes_profile_key_missing",
            "message": "cf-pwn missing key",
        }
    ]
    assert tasks.calls == []


def test_category_worker_skips_mismatched_payload(
    client: TestClient,
    session_factory: SessionFactory,
):
    first_task = _seed_designed_task(session_factory, task_no=1)
    second_task = _seed_designed_task(session_factory, task_no=2)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        first = _create_canonical_attempt(repo, first_task)
        second = _create_canonical_attempt(repo, second_task)
        session.get(build_model.BuildAttempt, first.id).created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        session.get(build_model.BuildAttempt, second.id).created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    _write_pending_attempt(client, first, design_task_id=uuid4())
    _write_pending_attempt(client, second)
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks

    response = client.post(
        "/api/build-attempts/worker/start",
        json={"category": "web"},
    )

    assert response.status_code == 202
    assert tasks.calls == [("web", second.id)]


def test_exact_worker_rejects_terminal_missing_and_mismatched_attempts(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        terminal = repo.create_attempt(task_id, f"{uuid4()}.json")
        repo.update_to_terminal(terminal.id, status="failed", error="failed")
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks

    assert client.post("/api/build-attempts/not-a-uuid/worker/start").status_code == 404
    assert client.post(f"/api/build-attempts/{uuid4()}/worker/start").status_code == 404
    assert client.post(f"/api/build-attempts/{terminal.id}/worker/start").status_code == 409
    assert tasks.calls == []


def test_exact_worker_recovers_staging_and_respects_busy_guard(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)
    paths = client.app.state.project_paths
    staged = paths.build_attempt_staging / f"{attempt.id}.json"
    write_json(
        staged,
        {
            "build_attempt_id": str(attempt.id),
            "design_task_id": str(attempt.design_task_id),
            "challenges": [{"id": "web-0001", "category": "web"}],
        },
    )
    tasks = _StubBuildTaskManager((False, "another task is already running"))
    client.app.state.dashboard_tasks = tasks

    response = client.post(f"/api/build-attempts/{attempt.id}/worker/start")

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]
    assert not staged.exists()
    assert (paths.shards / "pending" / attempt.shard_basename).exists()
    assert tasks.calls == [("web", attempt.id)]


def test_exact_worker_start_marks_legacy_attempt_running(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)
    _write_pending_attempt(client, attempt)
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks

    response = client.post(f"/api/build-attempts/{attempt.id}/worker/start")

    assert response.status_code == 202
    with session_factory() as session:
        row = session.get(build_model.BuildAttempt, attempt.id)
        assert row.status == "running"
    assert tasks.calls == [("web", attempt.id)]


def test_exact_worker_start_leaves_execution_backed_attempt_queued_until_cli_claim(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        attempt = _create_canonical_attempt(repo, task_id)
        execution = ExecutionsRepository(session).schedule_execution(
            attempt.id,
            execution_kind="initial",
        )
    _write_pending_attempt(client, attempt)
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks

    response = client.post(f"/api/build-attempts/{attempt.id}/worker/start")

    assert response.status_code == 202
    with session_factory() as session:
        attempt_row = session.get(build_model.BuildAttempt, attempt.id)
        execution_row = session.get(exec_model.Execution, execution.id)
        assert attempt_row.status == "queued"
        assert attempt_row.worker == "dashboard-01"
        assert attempt_row.latest_execution_id == execution.id
        assert execution_row.status == "queued"
        assert execution_row.worker_id is None
        assert execution_row.lease_expires_at is None
    assert tasks.calls == [("web", attempt.id)]


def test_exact_worker_start_rejects_existing_dashboard_worker_attempt(
    client: TestClient,
    session_factory: SessionFactory,
):
    first_task = _seed_designed_task(session_factory, status="building", task_no=1)
    second_task = _seed_designed_task(session_factory, task_no=2)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        running = _create_canonical_attempt(repo, first_task)
        queued = _create_canonical_attempt(repo, second_task)
        row = session.get(build_model.BuildAttempt, running.id)
        row.status = "running"
        row.worker = "dashboard-01"
    _write_pending_attempt(client, queued)
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks

    response = client.post(f"/api/build-attempts/{queued.id}/worker/start")

    assert response.status_code == 409
    assert "dashboard-01 is already running" in response.json()["detail"]
    assert tasks.calls == []


def test_exact_worker_accepts_iteration_shard_basename(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)
        iteration_basename = f"{attempt.id}.iter-001.json"
        session.get(build_model.BuildAttempt, attempt.id).shard_basename = iteration_basename
    attempt = SimpleNamespace(
        id=attempt.id,
        design_task_id=attempt.design_task_id,
        shard_basename=iteration_basename,
    )
    _write_pending_attempt(client, attempt)
    tasks = _StubBuildTaskManager()
    client.app.state.dashboard_tasks = tasks

    response = client.post(f"/api/build-attempts/{attempt.id}/worker/start")

    assert response.status_code == 202
    assert tasks.calls == [("web", attempt.id)]


def test_stop_build_worker_endpoint_terminates_dashboard_task(
    client: TestClient,
):
    tasks = _StubBuildTaskManager((True, "已结束 worker（1 个进程）"))
    client.app.state.dashboard_tasks = tasks

    response = client.post("/api/build-attempts/worker/stop")

    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True
    assert "已结束" in body["message"]
    assert body["state"]["running"] is False


def test_list_syncs_finished_dashboard_worker_to_lost(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory, status="building")
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)
        row = session.get(build_model.BuildAttempt, attempt.id)
        row.status = "running"
        row.worker = "dashboard-01"
    tasks = _StubBuildTaskManager()
    tasks.finished_records = [
        {
            "kind": "worker",
            "worker_ids": ["dashboard-01"],
            "returncode": -9,
        }
    ]
    client.app.state.dashboard_tasks = tasks

    response = client.get("/api/build-attempts")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == str(attempt.id)
    assert payload[0]["status"] == "lost"
    assert "dashboard worker exited" in payload[0]["error"]
    with session_factory() as session:
        row = session.get(build_model.BuildAttempt, attempt.id)
        assert row.status == "lost"
        assert session.get(task_model.DesignTask, task_id).status == "build_failed"


def test_finished_dashboard_worker_with_attempt_id_does_not_mark_sibling_lost(
    client: TestClient,
    session_factory: SessionFactory,
):
    first_task = _seed_designed_task(session_factory, status="building", task_no=1)
    second_task = _seed_designed_task(session_factory, status="building", task_no=2)
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        failed_process_attempt = _create_canonical_attempt(repo, first_task)
        sibling = _create_canonical_attempt(repo, second_task)
        for attempt in (failed_process_attempt, sibling):
            row = session.get(build_model.BuildAttempt, attempt.id)
            row.status = "running"
            row.worker = "dashboard-01"
    tasks = _StubBuildTaskManager()
    tasks.finished_records = [
        {
            "kind": "worker",
            "worker_ids": ["dashboard-01"],
            "build_attempt_ids": [str(failed_process_attempt.id)],
            "returncode": 1,
        }
    ]
    client.app.state.dashboard_tasks = tasks

    response = client.get("/api/build-attempts")

    assert response.status_code == 200
    with session_factory() as session:
        failed_row = session.get(build_model.BuildAttempt, failed_process_attempt.id)
        sibling_row = session.get(build_model.BuildAttempt, sibling.id)
        assert failed_row.status == "lost"
        assert sibling_row.status == "running"


def test_finished_old_lane_does_not_mark_new_lane_attempt_lost(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory, status="building")
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)
        row = session.get(build_model.BuildAttempt, attempt.id)
        row.status = "running"
        row.worker = "dashboard-lane-01-newpool"
    tasks = _StubBuildTaskManager()
    tasks.finished_records = [
        {
            "kind": "lane",
            "worker_ids": ["dashboard-lane-01-oldpool"],
            "build_attempt_ids": [str(attempt.id)],
            "returncode": 0,
        }
    ]
    client.app.state.dashboard_tasks = tasks

    response = client.get("/api/build-attempts")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == str(attempt.id)
    assert payload[0]["status"] == "running"
    with session_factory() as session:
        row = session.get(build_model.BuildAttempt, attempt.id)
        assert row.status == "running"
        assert row.error is None
        assert session.get(task_model.DesignTask, task_id).status == "building"


# ============================================================================
# Phase 0 restore endpoint — operator escape hatch for false-lost rows
# ============================================================================


def test_restore_brings_lost_attempt_back_when_shard_still_present(
    client: TestClient,
    session_factory: SessionFactory,
):
    """Operator can restore a wrongly-marked-lost attempt if its shard file
    is physically still in the queue."""
    task_id = _seed_designed_task(session_factory)
    paths = client.app.state.project_paths
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)
        # Mark lost (simulating the race-condition victim)
        row = session.get(build_model.BuildAttempt, attempt.id)
        row.status = "lost"
        row.finished_at = datetime.now(timezone.utc)
        row.error = "attributed shard disappeared from all queue states"
        session.get(task_model.DesignTask, task_id).status = "build_failed"
    pending = paths.shards / "pending" / attempt.shard_basename
    pending.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        pending,
        {
            "build_attempt_id": str(attempt.id),
            "design_task_id": str(attempt.design_task_id),
            "challenges": [{"id": "web-0001", "category": "web"}],
        },
    )

    response = client.post(f"/api/build-attempts/{attempt.id}/restore")

    assert response.status_code == 200
    body = response.json()
    assert body["build_attempt_id"] == str(attempt.id)
    assert body["restored_from"] == "lost"
    assert str(pending) in body["shard_found_at"]
    with session_factory() as session:
        restored = session.get(build_model.BuildAttempt, attempt.id)
        assert restored.status == "queued"
        assert restored.finished_at is None
        assert restored.error is None
        assert session.get(task_model.DesignTask, task_id).status == "building"


def test_restore_rejects_non_lost_attempt(
    client: TestClient,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)
        # status='queued' (default)

    response = client.post(f"/api/build-attempts/{attempt.id}/restore")

    assert response.status_code == 409
    assert "lost" in response.json()["detail"]
    assert "queued" in response.json()["detail"]


def test_restore_rejects_when_shard_file_truly_missing(
    client: TestClient,
    session_factory: SessionFactory,
):
    """If the shard is genuinely gone, restore must refuse — operator must
    use retry to create a fresh attempt instead."""
    task_id = _seed_designed_task(session_factory)
    with transaction(factory=session_factory) as session:
        attempt = _create_canonical_attempt(BuildAttemptsRepository(session), task_id)
        row = session.get(build_model.BuildAttempt, attempt.id)
        row.status = "lost"
        row.finished_at = datetime.now(timezone.utc)
        row.error = "attributed shard disappeared from all queue states"

    response = client.post(f"/api/build-attempts/{attempt.id}/restore")

    assert response.status_code == 409
    assert "not found" in response.json()["detail"]


def test_restore_returns_404_for_missing_attempt(client: TestClient):
    response = client.post(f"/api/build-attempts/{uuid4()}/restore")
    assert response.status_code == 404


# ============================================================================
# Failure message translation (status codes → Chinese for UI)
# ============================================================================


def test_failure_message_reason_translates_known_status_codes():
    """裸状态码 → 中文。"""
    from web.build_attempts_endpoints import _failure_message_reason

    cases = {
        "nonzero_exit": "参考解题脚本执行失败",
        "contract_failed": "合约校验未通过",
        "flag_mismatch": "解题脚本输出的 flag 与 metadata 中声明的不一致",
        "timeout": "参考解题脚本执行超时",
        "missing_validation": "缺少 validate.sh",
        "skipped_resume": "断点恢复跳过本次校验",
    }
    for code, expected_fragment in cases.items():
        translated = _failure_message_reason(code)
        assert expected_fragment in translated, f"code={code!r} expected '{expected_fragment}' in {translated!r}"


def test_failure_message_reason_translates_validator_format_string():
    """完整 "validator: status=X elapsed=..." 也能识别。"""
    from web.build_attempts_endpoints import _failure_message_reason

    msg = "validator: status=nonzero_exit elapsed=4.44s"
    translated = _failure_message_reason(msg)
    assert "参考解题脚本执行失败" in translated
    # 原始的 elapsed=4.44s 不再 leak 到 UI
    assert "elapsed=" not in translated


def test_failure_message_reason_extracts_error_marker_then_translates():
    """带 error= 详情时，提取 error 值并尝试翻译；翻译表没有就原样返回。"""
    from web.build_attempts_endpoints import _failure_message_reason

    # 命中翻译表的 error 值
    msg = "validator: status=contract_failed error=no compiled ELF artifact found in attachments/"
    translated = _failure_message_reason(msg)
    assert "未找到编译后的 ELF 产物" in translated

    # 未命中翻译表 → 原样返回 error 值
    msg = "infra: error=postgres connection refused"
    translated = _failure_message_reason(msg)
    assert translated == "postgres connection refused"


def test_failure_message_reason_returns_original_for_unknown():
    """未知状态码不破坏现有行为：原样返回（不假装翻译）。"""
    from web.build_attempts_endpoints import _failure_message_reason

    assert _failure_message_reason("some_brand_new_code") == "some_brand_new_code"
