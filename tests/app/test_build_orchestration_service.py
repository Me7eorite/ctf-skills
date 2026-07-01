"""PostgreSQL-backed tests for build submission and staging recovery."""

from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from core.jsonio import read_json, write_json
from core.paths import ProjectPaths
from domain.challenge_designs import ChallengeDesign
from domain.design_tasks import DesignTask
from persistence.models import build_attempts as build_model
from persistence.models import challenge_designs as design_model
from persistence.models import design_tasks as task_model
from persistence.models import executions as exec_model
from persistence.models import research as research_model
from persistence.models.progress import ProgressEvent
from persistence.repositories import BuildAttemptsRepository, ExecutionsRepository
from persistence.session import SessionFactory, transaction
from services import BuildOrchestrationError, BuildOrchestrationService
from services.build_orchestration_service import (
    MATRIX_FIELDS,
    STAGING_ORPHAN_GRACE_SECONDS,
)

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
    _clean_database(session_factory)
    yield
    _clean_database(session_factory)


def _clean_database(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        # Null container->execution pointers first to break the circular FK,
        # then delete executions / feedback before the containers.
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
        session.execute(sa.delete(design_model.DesignDifficultyReview))
        session.execute(sa.delete(design_model.ChallengeDesign))
        session.execute(sa.delete(design_model.DesignAttempt))
        session.execute(sa.delete(task_model.DesignTask))
        session.execute(sa.delete(research_model.ResearchRun))
        session.execute(sa.delete(research_model.GenerationRequest))
        session.commit()


def _payload(challenge_id: str, *, port: int = 8081, difficulty: str = "easy") -> dict:
    return {
        "event": {"flag_format": "flag{...}"},
        "challenges": [
            {
                "id": challenge_id,
                "title": "Blind Login",
                "category": "web",
                "difficulty": difficulty,
                "points": 100,
                "deployment": f"docker compose service on port {port}",
                "port": port,
                "primary_technique": "boolean blind sqli",
                "learning_objective": "Extract data through boolean responses.",
                "prompt": "Recover the admin note.",
                "flag_location": "FLAG environment variable",
                "validation": "Run the solver against localhost.",
                "hints": ["one", "two", "three"],
                "artifacts": ["README.md"],
                "implementation_plan": {
                    "runtime": "python:3.11-slim",
                    "framework": "Flask",
                },
            }
        ],
    }


def _seed_designed_task(
    session_factory: SessionFactory,
    *,
    task_no: int = 1,
    difficulty: str = "easy",
    design_payload: dict | None = None,
) -> UUID:
    with session_factory() as session:
        request = research_model.GenerationRequest(
            id=uuid4(),
            category="web",
            topic=f"topic-{uuid4()}",
            target_count=1,
            difficulty_distribution={difficulty: 1},
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
            challenge_id=f"web-{uuid4().hex[:8]}",
            title=f"Task {task_no}",
            category="web",
            difficulty=difficulty,
            primary_technique="boolean blind sqli",
            learning_objective="Extract data through boolean responses.",
            points=100,
            port=8080 + task_no,
            scenario="Distinct login response behavior with a realistic account recovery workflow.",
            constraints={},
            evidence_summary="",
            finding_ids=[],
            status="designed",
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
            payload=design_payload or _payload(task.challenge_id, port=task.port or 8081, difficulty=difficulty),
            summary="validated design",
            flag_format="flag{...}",
            validation_notes="passed",
            quality_gate_passed=True,
            status="draft",
        )
        session.add_all([request, run, task, design_attempt, design])
        session.commit()
        return task.id


def _service(
    tmp_path: Path,
    session_factory: SessionFactory,
) -> BuildOrchestrationService:
    return BuildOrchestrationService(
        paths=ProjectPaths(root=tmp_path, repository=tmp_path),
        session_factory=session_factory,
    )


def test_submit_batch_preserves_order_commits_and_publishes(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_a = _seed_designed_task(session_factory, task_no=1)
    task_b = _seed_designed_task(session_factory, task_no=2)
    service = _service(tmp_path, session_factory)

    attempt_ids = service.submit_batch([task_b, task_a])

    assert len(attempt_ids) == 2
    with session_factory() as session:
        attempts = [BuildAttemptsRepository(session).get(item) for item in attempt_ids]
        assert [item.design_task_id for item in attempts if item] == [task_b, task_a]
        assert all(session.get(task_model.DesignTask, task).status == "building" for task in [task_a, task_b])
        review_count = session.scalar(
            sa.select(sa.func.count()).select_from(design_model.DesignDifficultyReview)
        )
        assert review_count == 2
        shard_by_attempt = {
            item.id: item.shard_basename for item in attempts if item is not None
        }
    for attempt_id, task_id in zip(attempt_ids, [task_b, task_a], strict=True):
        payload = read_json(
            service.paths.shards / "pending" / shard_by_attempt[attempt_id],
            {},
        )
        assert payload["build_attempt_id"] == str(attempt_id)
        assert payload["design_task_id"] == str(task_id)
        assert "resume_from_shard_basename" not in payload
        assert set(payload["challenges"][0]) == set(MATRIX_FIELDS["web"]) | {"design"}
    assert service.paths.build_attempt_staging.is_dir()


def test_prebuild_difficulty_review_blocks_failed_medium_design(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    invalid_payload = _payload("web-invalid-medium", difficulty="medium")
    invalid_payload["challenges"][0].update(
        {
            "prompt": (
                "Recover the admin note from the support portal while preserving "
                "the expected customer support workflow."
            ),
            "techniques": ["boolean blind sqli", "idor"],
            "actual_solution_type": ["boolean blind sqli"],
            "unintended_solutions": ["Direct flag reads require admin session."],
        }
    )
    task_id = _seed_designed_task(
        session_factory,
        difficulty="medium",
        design_payload=invalid_payload,
    )
    service = _service(tmp_path, session_factory)

    with pytest.raises(BuildOrchestrationError) as excinfo:
        service.submit_single(task_id)

    assert excinfo.value.code == "difficulty_review_failed"
    assert "pre-build difficulty review" in str(excinfo.value)
    with session_factory() as session:
        assert BuildAttemptsRepository(session).list_for_design_task(task_id) == []
        task = session.get(task_model.DesignTask, task_id)
        assert task is not None and task.status == "queued"
        draft_count = session.scalar(
            sa.select(sa.func.count())
            .select_from(design_model.ChallengeDesign)
            .where(
                design_model.ChallengeDesign.design_task_id == task_id,
                design_model.ChallengeDesign.status == "draft",
            )
        )
        assert draft_count == 0
        superseded_count = session.scalar(
            sa.select(sa.func.count())
            .select_from(design_model.ChallengeDesign)
            .where(
                design_model.ChallengeDesign.design_task_id == task_id,
                design_model.ChallengeDesign.status == "superseded",
            )
        )
        assert superseded_count == 1
        latest_attempt = session.scalar(
            sa.select(design_model.DesignAttempt)
            .where(design_model.DesignAttempt.design_task_id == task_id)
            .order_by(design_model.DesignAttempt.attempt.desc())
            .limit(1)
        )
        assert latest_attempt is not None
        assert latest_attempt.status == "completed"
        assert latest_attempt.last_error is not None
        assert "Pre-build difficulty review failed" in latest_attempt.last_error
        review = session.scalar(
            sa.select(design_model.DesignDifficultyReview).where(
                design_model.DesignDifficultyReview.design_task_id == task_id
            )
        )
        assert review is not None
        assert review.passed is False
        assert review.actual_difficulty == "below_claimed"
        assert review.required_revision
    assert not list((service.paths.shards / "pending").glob("*.json"))


@pytest.mark.parametrize("category", ["web", "pwn", "re"])
def test_render_payload_uses_exact_category_matrix_fields(
    category: str,
    tmp_path: Path,
    session_factory: SessionFactory,
):
    now = datetime.now(timezone.utc)
    task = DesignTask(
        id=uuid4(),
        generation_request_id=uuid4(),
        research_run_id=uuid4(),
        task_no=1,
        challenge_id=f"{category}-0001",
        title="Matrix contract",
        category=category,
        difficulty="easy",
        primary_technique="test technique",
        learning_objective="test objective",
        points=100,
        port=8081 if category in {"web", "pwn"} else None,
        scenario="distinct scenario",
        constraints={},
        evidence_summary="",
        finding_ids=(),
        status="designed",
        created_at=now,
        updated_at=now,
    )
    design_payload = {
        "event": {"flag_format": "flag{...}"},
        "challenges": [{"deployment": "docker" if category != "re" else "download"}],
    }
    design = ChallengeDesign(
        id=uuid4(),
        design_task_id=task.id,
        design_attempt_id=uuid4(),
        payload=design_payload,
        summary="summary",
        flag_format="flag{...}",
        validation_notes="passed",
        quality_gate_passed=True,
        status="draft",
        created_at=now,
        updated_at=now,
    )

    payload = _service(tmp_path, session_factory).render_shard_payload(
        task,
        design,
        build_attempt_id=uuid4(),
    )

    assert set(payload["challenges"][0]) == set(MATRIX_FIELDS[category]) | {"design"}


def test_ineligible_batch_is_all_or_nothing(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    eligible = _seed_designed_task(session_factory, task_no=1)
    ineligible = _seed_designed_task(session_factory, task_no=2)
    with session_factory() as session:
        session.get(task_model.DesignTask, ineligible).status = "building"
        session.commit()
    service = _service(tmp_path, session_factory)

    with pytest.raises(BuildOrchestrationError, match="expected designed"):
        service.submit_batch([eligible, ineligible])

    with session_factory() as session:
        assert BuildAttemptsRepository(session).list_for_design_task(eligible) == []
    assert not list((service.paths.shards / "pending").glob("*.json"))
    assert not list(service.paths.build_attempt_staging.glob("*.json"))


def test_precommit_failure_removes_staged_payloads(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    task_id = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)

    def fail_commit(*args, **kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(service, "_commit", fail_commit)
    with pytest.raises(RuntimeError, match="database write failed"):
        service.submit_single(task_id)

    assert not list(service.paths.build_attempt_staging.glob("*"))
    assert not list((service.paths.shards / "pending").glob("*.json"))
    with session_factory() as session:
        assert BuildAttemptsRepository(session).list_for_design_task(task_id) == []
        assert session.get(task_model.DesignTask, task_id).status == "designed"


def test_postcommit_publication_failure_recovers_idempotently(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    task_id = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)
    original_publish = service._publish

    def fail_publish(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(service, "_publish", fail_publish)
    attempt_id = service.submit_single(task_id)
    staged = service.paths.build_attempt_staging / f"{attempt_id}.json"
    assert staged.exists()
    with session_factory() as session:
        assert BuildAttemptsRepository(session).get(attempt_id) is not None

    monkeypatch.setattr(service, "_publish", original_publish)
    assert service.recover_staging() == {attempt_id}
    with session_factory() as session:
        attempt = BuildAttemptsRepository(session).get(attempt_id)
        assert attempt is not None
        pending = service.paths.shards / "pending" / attempt.shard_basename
    assert pending.exists()
    assert not staged.exists()
    assert service.recover_staging() == set()
    assert pending.exists()


def test_publish_keeps_staging_when_pending_collision_is_mismatched(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    service = _service(tmp_path, session_factory)
    service.paths.initialize()
    attempt_id = uuid4()
    staged = service.paths.build_attempt_staging / f"{attempt_id}.json"
    shard_basename = f"{attempt_id}.json"
    pending = service.paths.shards / "pending" / shard_basename
    write_json(
        staged,
        {"build_attempt_id": str(attempt_id), "design_task_id": str(uuid4())},
    )
    write_json(pending, {"challenges": [{"id": "manual", "category": "web"}]})

    with pytest.raises(FileExistsError, match="another attempt"):
        service._publish(staged, shard_basename)

    assert staged.exists()
    assert read_json(pending, {})["challenges"][0]["id"] == "manual"

    write_json(pending, {"build_attempt_id": str(attempt_id)})
    service._publish(staged, shard_basename)
    assert not staged.exists()


def test_recovery_keeps_young_orphan_and_removes_old_orphan(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    service = _service(tmp_path, session_factory)
    service.paths.initialize()
    young = service.paths.build_attempt_staging / f"{uuid4()}.json"
    old = service.paths.build_attempt_staging / f"{uuid4()}.json"
    write_json(young, {})
    write_json(old, {})
    now = max(young.stat().st_mtime, old.stat().st_mtime)
    os.utime(old, (now - STAGING_ORPHAN_GRACE_SECONDS - 1,) * 2)

    service.recover_staging(now=now)

    assert young.exists()
    assert not old.exists()


def test_build_failed_submit_links_resume_and_stale_retry_is_rejected(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)
    first_id = service.submit_single(task_id)
    with transaction(factory=session_factory) as session:
        BuildAttemptsRepository(session).update_to_terminal(
            first_id,
            status="failed",
            error="failed validation",
        )
        session.get(task_model.DesignTask, task_id).status = "build_failed"

    second_id = service.submit_single(task_id)
    with session_factory() as session:
        first = BuildAttemptsRepository(session).get(first_id)
        second = BuildAttemptsRepository(session).get(second_id)
        assert first is not None
        assert second is not None
    second_payload = read_json(
        service.paths.shards / "pending" / second.shard_basename,
        {},
    )
    assert second_payload["resume_from_shard_basename"] == first.shard_basename

    with transaction(factory=session_factory) as session:
        BuildAttemptsRepository(session).update_to_terminal(
            second_id,
            status="lost",
        )
        session.get(task_model.DesignTask, task_id).status = "build_failed"
    with session_factory() as session:
        source_second = BuildAttemptsRepository(session).get(second_id)
        assert source_second is not None
        source_second_shard = source_second.shard_basename

    with pytest.raises(BuildOrchestrationError, match="latest"):
        service.retry(first_id)
    third_id = service.retry(second_id)
    with session_factory() as session:
        second = BuildAttemptsRepository(session).get(second_id)
        third = BuildAttemptsRepository(session).get(third_id)
        assert second is not None
        assert third is not None
    third_payload = read_json(
        service.paths.shards / "pending" / third.shard_basename,
        {},
    )
    assert third_payload["resume_from_shard_basename"] == source_second_shard
    assert third_payload["execution_mode"] == "resume"


def test_retry_reuses_active_attempt_when_task_already_has_one(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)

    source_id = service.submit_single(task_id)
    with transaction(factory=session_factory) as session:
        build_repo = BuildAttemptsRepository(session)
        build_repo.update_to_terminal(source_id, status="failed", error="boom")
        session.get(task_model.DesignTask, task_id).status = "build_failed"
        active = build_repo.create_attempt(task_id, f"{uuid4()}.json")
        session.get(task_model.DesignTask, task_id).status = "build_failed"

    retry_id = service.retry(source_id)

    assert retry_id == active.id

    with session_factory() as session:
        assert BuildAttemptsRepository(session).get(active.id) is not None


def test_clean_rebuild_is_confirmed_idempotent_and_omits_resume_source(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)
    source_id = service.submit_single(task_id)
    with transaction(factory=session_factory) as session:
        BuildAttemptsRepository(session).update_to_terminal(source_id, status="failed", error="failed validation")
        session.get(task_model.DesignTask, task_id).status = "build_failed"

    with pytest.raises(BuildOrchestrationError, match="confirmation_required"):
        service.clean_rebuild(source_id, idempotency_key="clean-key", confirmed=False)

    clean_id = service.clean_rebuild(source_id, idempotency_key="clean-key", confirmed=True)
    assert clean_id != source_id
    assert service.clean_rebuild(source_id, idempotency_key="clean-key", confirmed=True) == clean_id
    payload = read_json(
        service.paths.shards / "pending" / f"{clean_id}.iter-001.json",
        {},
    )
    assert payload["execution_mode"] == "clean"
    assert "resume_from_shard_basename" not in payload
    with session_factory() as session:
        clean_row = session.get(build_model.BuildAttempt, clean_id)
        assert clean_row is not None
        assert clean_row.shard_basename == f"{clean_id}.iter-001.json"
        assert clean_row.idempotency_key == "clean-key"
        assert session.scalar(
            sa.select(sa.func.count()).select_from(build_model.BuildAttempt)
        ) == 2
    with pytest.raises(BuildOrchestrationError) as different_key:
        service.clean_rebuild(source_id, idempotency_key="different-key", confirmed=True)
    assert different_key.value.code == "stale_source_attempt"


def test_concurrent_same_key_clean_rebuild_collapses_to_one_attempt(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    task_id = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)
    source_id = service.submit_single(task_id)
    with transaction(factory=session_factory) as session:
        BuildAttemptsRepository(session).update_to_terminal(source_id, status="failed")
        session.get(task_model.DesignTask, task_id).status = "build_failed"

    barrier = threading.Barrier(2)
    original_write = BuildOrchestrationService._write_staged_payload

    def synchronized_write(self, submission):
        barrier.wait(timeout=5)
        return original_write(self, submission)

    monkeypatch.setattr(
        BuildOrchestrationService,
        "_write_staged_payload",
        synchronized_write,
    )
    key = f"concurrent-{uuid4()}"

    def submit() -> UUID:
        return _service(tmp_path, session_factory).clean_rebuild(
            source_id,
            idempotency_key=key,
            confirmed=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: submit(), range(2)))

    assert results[0] == results[1]
    with session_factory() as session:
        count = session.scalar(
            sa.select(sa.func.count())
            .select_from(build_model.BuildAttempt)
            .where(build_model.BuildAttempt.idempotency_key == key)
        )
        row = session.scalar(
            sa.select(build_model.BuildAttempt).where(
                build_model.BuildAttempt.idempotency_key == key
            )
        )
    assert count == 1
    assert row is not None
    assert row.id != source_id
    assert row.shard_basename == f"{row.id}.iter-001.json"


def test_default_retry_reuses_container_and_appends_execution(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch,
):
    monkeypatch.delenv("EXECUTION_MINTING", raising=False)
    task = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)

    [container_id] = service.submit_batch([task])

    # Fresh submit scheduled the initial execution.
    with session_factory() as session:
        repo = ExecutionsRepository(session)
        execs = repo.list_for_attempt(container_id)
        assert [e.iteration_no for e in execs] == [1]
        assert execs[0].execution_kind == "initial"
        # Simulate the worker driving iteration 1 to a failed terminal.
        _, token = repo.claim_queued(
            container_id, worker_id="w", lease_ttl_seconds=300
        )
        repo.update_to_terminal(execs[0].id, claim_token=token, status="failed")
        session.get(task_model.DesignTask, task).status = "build_failed"
        session.commit()

    retry_id = service.retry(container_id)

    # Same container id — no new build_attempt minted.
    assert retry_id == container_id
    with session_factory() as session:
        repo = ExecutionsRepository(session)
        execs = repo.list_for_attempt(container_id)
        assert [e.iteration_no for e in execs] == [1, 2]
        assert execs[1].execution_kind == "retry"
        assert execs[1].parent_execution_id == execs[0].id
        assert execs[1].status == "queued"
        count = session.scalar(
            sa.select(sa.func.count()).select_from(build_model.BuildAttempt)
        )
        assert count == 1
        container = session.get(build_model.BuildAttempt, container_id)
        assert container.shard_basename == f"{container_id}.iter-002.json"
    # Per-iteration shard published to pending.
    assert (
        service.paths.shards / "pending" / f"{container_id}.iter-002.json"
    ).exists()
    with session_factory() as session:
        claimed, token = ExecutionsRepository(session).claim_queued(
            container_id, worker_id="retry-worker", lease_ttl_seconds=300
        )
        assert claimed.iteration_no == 2
        assert claimed.claim_token == token
        assert claimed.status == "claimed"


def test_repair_reuses_container_and_carries_failure_context(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch,
):
    monkeypatch.delenv("EXECUTION_MINTING", raising=False)
    task = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)

    [container_id] = service.submit_batch([task])
    with session_factory() as session:
        repo = ExecutionsRepository(session)
        [initial] = repo.list_for_attempt(container_id)
        _, token = repo.claim_queued(
            container_id, worker_id="w", lease_ttl_seconds=300
        )
        repo.update_to_terminal(
            initial.id,
            claim_token=token,
            status="failed",
            error="contract_failed: solver references metadata.json",
        )
        session.get(task_model.DesignTask, task).status = "build_failed"
        session.commit()

    repair_id = service.repair(container_id)

    assert repair_id == container_id
    payload = read_json(
        service.paths.shards / "pending" / f"{container_id}.iter-002.json",
        {},
    )
    assert payload["execution_mode"] == "clean"
    assert "resume_from_shard_basename" not in payload
    assert payload["repair_requested"] is True
    assert payload["repair_context"]["source_build_attempt_id"] == str(container_id)
    assert "metadata.json" in payload["repair_context"]["failure_summary"]
    with session_factory() as session:
        execs = ExecutionsRepository(session).list_for_attempt(container_id)
        assert [item.iteration_no for item in execs] == [1, 2]
        assert execs[1].execution_kind == "retry"
        assert execs[1].parent_execution_id == execs[0].id


def test_repair_lost_attempt_uses_progress_failure_instead_of_lease_error(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch,
):
    monkeypatch.delenv("EXECUTION_MINTING", raising=False)
    task = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)

    [container_id] = service.submit_batch([task])
    with session_factory() as session:
        repo = ExecutionsRepository(session)
        [initial] = repo.list_for_attempt(container_id)
        _, token = repo.claim_queued(
            container_id, worker_id="w", lease_ttl_seconds=300
        )
        repo.update_to_terminal(
            initial.id,
            claim_token=token,
            status="lost",
            error="lease expired",
        )
        session.add(
            ProgressEvent(
                shard=f"{container_id}.iter-001.json",
                challenge_id="re-3ce05a7b-0013",
                worker="dashboard-01",
                stage="complete",
                status="failed",
                percent=99,
                message="contract_failed (implement evidence incomplete)",
            )
        )
        session.get(task_model.DesignTask, task).status = "build_failed"
        session.commit()

    repair_id = service.repair(container_id)

    assert repair_id == container_id
    payload = read_json(
        service.paths.shards / "pending" / f"{container_id}.iter-002.json",
        {},
    )
    summary = payload["repair_context"]["failure_summary"]
    assert payload["execution_mode"] == "clean"
    assert payload["repair_requested"] is True
    assert "implement evidence incomplete" in summary
    assert "lease expired" not in summary


def test_retry_carries_forward_workspace_failure_context(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch,
):
    monkeypatch.delenv("EXECUTION_MINTING", raising=False)
    task = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)

    [container_id] = service.submit_batch([task])
    workspace_state = service.paths.executions / str(container_id) / "current" / "state"
    write_json(
        workspace_state / "first-validation-failure.json",
        {
            "round": 0,
            "results": [
                {
                    "challenge_id": "web-0001",
                    "solve_status": "failed",
                    "validation_status": "contract_failed",
                    "validation_error": "docker build failed with exit 127",
                    "failure_kind": "missing_dependency",
                    "failure_hint": "Install make in the Dockerfile.",
                    "failed_step": "Step 7: RUN make",
                }
            ],
        },
    )
    write_json(
        workspace_state / "validation-history.json",
        [
            {
                "round": 0,
                "results": [
                    {
                        "challenge_id": "web-0001",
                        "solve_status": "failed",
                        "validation_status": "contract_failed",
                        "validation_error": "docker build failed with exit 127",
                        "failure_kind": "missing_dependency",
                        "failure_hint": "Install make in the Dockerfile.",
                        "failed_step": "Step 7: RUN make",
                    }
                ],
            },
            {
                "round": 1,
                "results": [
                    {
                        "challenge_id": "web-0001",
                        "solve_status": "failed",
                        "validation_status": "contract_failed",
                        "validation_error": "COPY failed: file not found in build context",
                        "failure_kind": "missing_source",
                        "failure_hint": "Use deploy/src paths in Dockerfile COPY commands.",
                        "failed_step": "Step 5: COPY src/app.py /app/app.py",
                    }
                ],
            },
        ],
    )
    with session_factory() as session:
        repo = ExecutionsRepository(session)
        [initial] = repo.list_for_attempt(container_id)
        _, token = repo.claim_queued(
            container_id, worker_id="w", lease_ttl_seconds=300
        )
        repo.update_to_terminal(
            initial.id,
            claim_token=token,
            status="failed",
            error="contract_failed: docker build failed",
        )
        session.get(task_model.DesignTask, task).status = "build_failed"
        session.commit()

    retry_id = service.retry(container_id)

    assert retry_id == container_id
    payload = read_json(
        service.paths.shards / "pending" / f"{container_id}.iter-002.json",
        {},
    )
    assert payload["retry_context"]["source_build_attempt_id"] == str(container_id)
    assert payload["retry_context"]["first_failure"]["failure_hint"] == "Install make in the Dockerfile."
    assert payload["retry_context"]["latest_failure"]["failure_kind"] == "missing_source"
    assert "repair_requested" not in payload


def test_legacy_retry_still_mints_new_attempt_when_explicitly_disabled(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch,
):
    monkeypatch.setenv("EXECUTION_MINTING", "0")
    task = _seed_designed_task(session_factory)
    service = _service(tmp_path, session_factory)

    [first_id] = service.submit_batch([task])
    with session_factory() as session:
        BuildAttemptsRepository(session).update_to_terminal(
            first_id, status="failed", error="boom"
        )
        session.get(task_model.DesignTask, task).status = "build_failed"
        session.commit()

    retry_id = service.retry(first_id)

    # Legacy path: a brand-new build_attempt row, and no executions created.
    assert retry_id != first_id
    with session_factory() as session:
        count = session.scalar(
            sa.select(sa.func.count()).select_from(build_model.BuildAttempt)
        )
        assert count == 2
        exec_count = session.scalar(
            sa.select(sa.func.count()).select_from(exec_model.Execution)
        )
        assert exec_count == 0
