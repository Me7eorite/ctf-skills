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

from core.jsonio import write_json
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


class _StubBuildTaskManager:
    def __init__(self, response: tuple[bool, str] = (True, "worker started")):
        self.response = response
        self.calls: list[tuple[str, UUID]] = []

    def start_worker(self, *, category: str, build_attempt_id: UUID):
        self.calls.append((category, build_attempt_id))
        return self.response

    def start_sequential_worker(self, *, build_attempt_ids: list[UUID]):
        self.calls.extend(("sequence", attempt_id) for attempt_id in build_attempt_ids)
        return self.response


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


def _write_pending_attempt(
    client: TestClient,
    attempt,
    *,
    category: str = "web",
    design_task_id: UUID | None = None,
) -> Path:
    path = (
        client.app.state.project_paths.shards
        / "pending"
        / attempt.shard_basename
    )
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
    assert (
        client.post("/api/design-tasks/build", json={"design_task_ids": ["nope"]})
        .status_code
        == 400
    )
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

    response = client.post(
        "/api/build-attempts/worker/start-sequential",
        json={"build_attempt_ids": [str(second.id), str(first.id)]},
    )

    assert response.status_code == 202
    assert response.json()["build_attempt_ids"] == [str(second.id), str(first.id)]
    assert response.json()["queue_length"] == 2
    assert tasks.calls == [("sequence", second.id), ("sequence", first.id)]


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
        session.get(build_model.BuildAttempt, first.id).created_at = datetime(
            2026, 1, 1, tzinfo=timezone.utc
        )
        session.get(build_model.BuildAttempt, second.id).created_at = datetime(
            2026, 1, 2, tzinfo=timezone.utc
        )

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
        assert expected_fragment in translated, (
            f"code={code!r} expected '{expected_fragment}' in {translated!r}"
        )


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
    msg = "validator: status=contract_failed error=no compiled ELF artifact found in attachments/ or dist/"
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
