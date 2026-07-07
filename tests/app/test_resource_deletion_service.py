"""PostgreSQL service tests for resource deletion."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

import services.resource_deletion_service as deletion_module
from core.jsonio import write_json
from core.paths import ProjectPaths
from core.state import InMemoryProgressStore
from persistence.models import build_attempts as build_model
from persistence.models import artifact_observations as observation_model
from persistence.models import challenge_corpus as corpus_model
from persistence.models import challenge_designs as design_model
from persistence.models import design_profile_reservations as reservation_model
from persistence.models import design_tasks as task_model
from persistence.models import research as research_model
from persistence.repositories import BuildAttemptsRepository
from persistence.repositories.progress import PostgresProgressStore
from persistence.session import SessionFactory
from services import ResourceDeletionConflictError, ResourceDeletionService

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


def _reset_schema(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


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
    factory = SessionFactory(create_engine(url))
    yield factory
    _reset_schema(url)


@pytest.fixture(autouse=True)
def clean_db(session_factory: SessionFactory):
    with session_factory() as session:
        with session.begin():
            session.execute(sa.delete(corpus_model.CorpusReviewDecision))
            session.execute(sa.delete(corpus_model.ObservationReviewDecision))
            session.execute(sa.delete(corpus_model.CorpusDecision))
            session.execute(sa.delete(corpus_model.CorpusMatch))
            session.execute(sa.delete(corpus_model.CorpusBatchMember))
            session.execute(sa.delete(corpus_model.CorpusBatch))
            session.execute(sa.delete(corpus_model.CorpusHistoryEntry))
            session.execute(
                sa.update(task_model.DesignTask).values(
                    current_reservation_id=None,
                    current_design_evidence_id=None,
                )
            )
            session.execute(
                sa.update(build_model.BuildAttempt).values(
                    design_evidence_id=None,
                    artifact_observation_id=None,
                )
            )
            session.execute(sa.delete(observation_model.ArtifactObservation))
            session.execute(sa.delete(design_model.DesignEvidence))
            session.execute(sa.delete(reservation_model.DesignProfileReservation))
            session.execute(sa.delete(reservation_model.DesignProfileLedger))
            session.execute(sa.delete(build_model.BuildAttempt))
            session.execute(sa.delete(task_model.DesignTask))
            session.execute(text("DELETE FROM research_runs"))
            session.execute(text("DELETE FROM generation_requests"))


def _seed_task(session_factory: SessionFactory, *, status: str = "build_failed"):
    request_id = uuid4()
    run_id = uuid4()
    task_id = uuid4()
    with session_factory() as session:
        with session.begin():
            session.execute(
                text(
                    "INSERT INTO generation_requests "
                    "(id, category, topic, target_count, difficulty_distribution, status) "
                    "VALUES (:id, 'web', 'Delete demo', 1, '{\"easy\": 1}'::jsonb, 'researched')"
                ),
                {"id": request_id},
            )
            session.execute(
                text(
                    "INSERT INTO research_runs "
                    "(id, generation_request_id, attempt, status) "
                    "VALUES (:id, :request_id, 1, 'completed')"
                ),
                {"id": run_id, "request_id": request_id},
            )
            session.execute(
                text(
                    "INSERT INTO design_tasks "
                    "(id, generation_request_id, research_run_id, task_no, challenge_id, "
                    "title, category, difficulty, primary_technique, learning_objective, "
                    "points, status, next_build_attempt_no) "
                    "VALUES (:id, :request_id, :run_id, 1, 'web-0001', "
                    "'Demo', 'web', 'easy', 'delete', 'Delete safely', 100, :status, 3)"
                ),
                {
                    "id": task_id,
                    "request_id": request_id,
                    "run_id": run_id,
                    "status": status,
                },
            )
    return task_id


def test_delete_only_failed_attempt_cleans_operational_state_and_retains_artifact(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    attempt_id = uuid4()
    shard = f"{attempt_id}.json"
    artifact = paths.challenges / "web" / "web-0001-demo"
    artifact.mkdir(parents=True)
    (artifact / "metadata.json").write_text("{}", encoding="utf-8")
    done_shard = paths.shards / "done" / shard
    write_json(done_shard, {"build_attempt_id": str(attempt_id)})
    progress = InMemoryProgressStore()
    progress.record(shard=shard, stage="queued", status="running")

    with session_factory() as session:
        with session.begin():
            session.add(
                build_model.BuildAttempt(
                    id=attempt_id,
                    design_task_id=task_id,
                    attempt_no=1,
                    status="failed",
                    shard_basename=shard,
                    resulting_challenge_dir=str(artifact.relative_to(tmp_path)),
                    artifact_status="present",
                )
            )

    result = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=progress,
    ).delete_build_attempt(attempt_id)

    assert len(result.retained) == 1
    assert result.retained[0].path == str(artifact)
    assert artifact.exists()
    assert not done_shard.exists()
    assert progress.events_for_shard(shard) == []
    with session_factory() as session:
        assert session.get(build_model.BuildAttempt, attempt_id) is None
        task = session.get(task_model.DesignTask, task_id)
        assert task is not None
        assert task.status == "designed"
        assert task.next_build_attempt_no == 3
    assert not (paths.work / "deletion-quarantine").exists()


def test_delete_attempt_removes_mutable_governance_and_retains_history(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory, status="built")
    attempt_id = uuid4()
    design_attempt_id = uuid4()
    design_id = uuid4()
    evidence_id = uuid4()
    observation_id = uuid4()
    batch_id = uuid4()
    member_id = uuid4()
    decision_id = uuid4()
    review_id = uuid4()
    history_id = uuid4()
    with session_factory() as session:
        with session.begin():
            session.add(
                design_model.DesignAttempt(
                    id=design_attempt_id,
                    design_task_id=task_id,
                    attempt=1,
                    status="completed",
                    claim_token=uuid4(),
                    profile_name_used="default",
                )
            )
            session.add(
                design_model.ChallengeDesign(
                    id=design_id,
                    design_task_id=task_id,
                    design_attempt_id=design_attempt_id,
                    payload={},
                    summary="demo",
                    flag_format="flag{...}",
                    validation_notes="ok",
                    quality_gate_passed=True,
                    status="accepted",
                )
            )
            session.flush()
            session.add(
                design_model.DesignEvidence(
                    id=evidence_id,
                    design_task_id=task_id,
                    evidence_version=1,
                    challenge_design_id=design_id,
                    research_finding_ids=[],
                    profile={},
                    profile_signature="profile",
                    distinctness_claim="distinct",
                    compared_challenge_ids=[],
                    evidence={},
                    build_contract={},
                    ledger_version=1,
                )
            )
            session.flush()
            session.add(
                build_model.BuildAttempt(
                    id=attempt_id,
                    design_task_id=task_id,
                    attempt_no=1,
                    status="succeeded",
                    shard_basename=f"{attempt_id}.json",
                    artifact_status="present",
                    design_evidence_id=evidence_id,
                    contract_sha256="contract",
                )
            )
            session.flush()
            session.add(
                observation_model.ArtifactObservation(
                    id=observation_id,
                    build_attempt_id=attempt_id,
                    observation_version=1,
                    design_evidence_id=evidence_id,
                    contract_sha256="contract",
                    artifact_manifest_sha256="artifact",
                    observed_profile={},
                    contract_checks={},
                    negative_test_results={},
                    fingerprints={"combined": "abc"},
                    status="passed",
                    is_current=True,
                )
            )
            session.flush()
            session.add(
                corpus_model.CorpusBatch(
                    id=batch_id,
                    mode="production",
                    category="web",
                    policy_version=1,
                    status="evaluated",
                    created_by="operator",
                )
            )
            session.flush()
            session.add(
                corpus_model.CorpusBatchMember(
                    id=member_id,
                    batch_id=batch_id,
                    build_attempt_id=attempt_id,
                    design_evidence_id=evidence_id,
                    artifact_observation_id=observation_id,
                    fingerprint_version=1,
                    fingerprints={"combined": "abc"},
                )
            )
            session.flush()
            session.add(
                corpus_model.CorpusDecision(
                    id=decision_id,
                    batch_id=batch_id,
                    member_id=member_id,
                    scope="member",
                    decision="review_required",
                    reasons=["source_similarity_review"],
                    policy_version=1,
                    is_current=True,
                )
            )
            session.flush()
            session.add(
                corpus_model.CorpusReviewDecision(
                    id=review_id,
                    corpus_decision_id=decision_id,
                    decision="approved",
                    actor="operator",
                    reason="reviewed",
                    scope="production-publication",
                )
            )
            session.flush()
            session.add(
                corpus_model.CorpusHistoryEntry(
                    id=history_id,
                    challenge_id="web-0001",
                    category="web",
                    design_evidence_id=evidence_id,
                    build_attempt_id=attempt_id,
                    artifact_observation_id=observation_id,
                    fingerprint_version=1,
                    fingerprints={"combined": "abc"},
                    status="published",
                    audit_reason="published batch",
                )
            )

    result = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).delete_build_attempt(attempt_id)

    assert result.retained_governance_history == [
        deletion_module.ArtifactOutcome(
            str(history_id),
            "corpus_history:published:web-0001:decisions=1:corpus_reviews=1:observation_reviews=0",
        )
    ]
    with session_factory() as session:
        assert session.get(build_model.BuildAttempt, attempt_id) is None
        assert session.get(observation_model.ArtifactObservation, observation_id) is None
        assert session.get(corpus_model.CorpusBatchMember, member_id) is None
        assert session.get(corpus_model.CorpusDecision, decision_id) is None
        assert session.get(corpus_model.CorpusReviewDecision, review_id) is None
        assert session.get(design_model.DesignEvidence, evidence_id) is not None
        history = session.get(corpus_model.CorpusHistoryEntry, history_id)
        assert history is not None
        assert history.build_attempt_id is None
        assert history.design_evidence_id is None
        assert history.artifact_observation_id is None
        assert history.audit_reason == (
            "published batch; retained during resource deletion; "
            "detached_decisions=1; detached_corpus_reviews=1; "
            "detached_observation_reviews=0"
        )
        retained = history.fingerprints["retained_governance_history"]
        assert retained["reason"] == "resource_deletion"
        assert retained["detached_decisions"][0]["decision"] == "review_required"
        assert retained["detached_decisions"][0]["reasons"] == [
            "source_similarity_review"
        ]
        assert retained["detached_corpus_reviews"][0]["actor"] == "operator"
        assert retained["detached_corpus_reviews"][0]["reason"] == "reviewed"
        assert retained["detached_corpus_reviews"][0]["scope"] == (
            "production-publication"
        )


def test_delete_attempt_rejects_active_sibling(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory, status="building")
    failed_id = uuid4()
    queued_id = uuid4()
    with session_factory() as session:
        with session.begin():
            session.add_all(
                [
                    build_model.BuildAttempt(
                        id=failed_id,
                        design_task_id=task_id,
                        attempt_no=1,
                        status="failed",
                        shard_basename=f"{failed_id}.json",
                    ),
                    build_model.BuildAttempt(
                        id=queued_id,
                        design_task_id=task_id,
                        attempt_no=2,
                        status="queued",
                        shard_basename=f"{queued_id}.json",
                    ),
                ]
            )

    service = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    )
    with pytest.raises(ResourceDeletionConflictError):
        service.delete_build_attempt(failed_id)


def test_quarantine_recovery_restores_when_root_still_exists(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    operation = paths.work / "deletion-quarantine" / "restore-op"
    destination = operation / "0000-shard.json"
    source = paths.shards / "pending" / "restore-shard.json"
    write_json(destination, {"build_attempt_id": "demo"})
    write_json(
        operation / "manifest.json",
        {
            "root_resource": {"type": "design_task", "id": str(task_id)},
            "entries": [
                {
                    "source": str(source),
                    "destination": str(destination),
                    "state": "quarantined",
                }
            ],
        },
    )

    warnings = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).recover_quarantine()

    assert warnings == []
    assert source.exists()
    assert not operation.exists()


def test_quarantine_recovery_purges_when_root_was_deleted(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    operation = paths.work / "deletion-quarantine" / "purge-op"
    destination = operation / "0000-shard.json"
    source = paths.shards / "done" / "deleted-shard.json"
    write_json(destination, {"build_attempt_id": "demo"})
    write_json(
        operation / "manifest.json",
        {
            "root_resource": {
                "type": "build_attempt",
                "id": str(uuid4()),
            },
            "entries": [
                {
                    "source": str(source),
                    "destination": str(destination),
                    "state": "quarantined",
                }
            ],
        },
    )

    warnings = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).recover_quarantine()

    assert warnings == []
    assert not source.exists()
    assert not operation.exists()


def test_explicit_artifact_delete_reports_only_artifact_paths(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    attempt_id = uuid4()
    shard = f"{attempt_id}.json"
    artifact = paths.challenges / "web" / "web-0001-explicit"
    artifact.mkdir(parents=True)
    done_shard = paths.shards / "done" / shard
    write_json(done_shard, {"build_attempt_id": str(attempt_id)})
    with session_factory() as session:
        with session.begin():
            session.add(
                build_model.BuildAttempt(
                    id=attempt_id,
                    design_task_id=task_id,
                    attempt_no=1,
                    status="failed",
                    shard_basename=shard,
                    resulting_challenge_dir=str(artifact.relative_to(tmp_path)),
                    artifact_status="present",
                )
            )

    result = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).delete_build_attempt(attempt_id, delete_artifacts=True)

    assert result.deleted == [str(artifact)]
    assert not artifact.exists()
    assert not done_shard.exists()


def test_explicit_delete_refuses_artifact_root_and_category_directory(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    attempt_id = uuid4()
    with session_factory() as session:
        with session.begin():
            session.add(
                build_model.BuildAttempt(
                    id=attempt_id,
                    design_task_id=task_id,
                    attempt_no=1,
                    status="failed",
                    shard_basename=f"{attempt_id}.json",
                    resulting_challenge_dir=str(
                        (paths.challenges / "web").relative_to(tmp_path)
                    ),
                    artifact_status="present",
                )
            )

    result = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).delete_build_attempt(attempt_id, delete_artifacts=True)

    assert (paths.challenges / "web").is_dir()
    assert [(item.path, item.reason) for item in result.skipped] == [
        (str((paths.challenges / "web").relative_to(tmp_path)), "unsafe-path")
    ]


def test_delete_design_task_orders_restrict_children_and_keeps_request(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory, status="designed")
    design_attempt_id = uuid4()
    design_id = uuid4()
    with session_factory() as session:
        task = session.get(task_model.DesignTask, task_id)
        assert task is not None
        request_id = task.generation_request_id
        with session.begin_nested():
            session.add(
                design_model.DesignAttempt(
                    id=design_attempt_id,
                    design_task_id=task_id,
                    attempt=1,
                    status="completed",
                    claim_token=uuid4(),
                    profile_name_used="default",
                )
            )
            session.add(
                design_model.ChallengeDesign(
                    id=design_id,
                    design_task_id=task_id,
                    design_attempt_id=design_attempt_id,
                    payload={},
                    summary="demo",
                    flag_format="flag{...}",
                    validation_notes="ok",
                    quality_gate_passed=True,
                    status="draft",
                )
            )
        session.commit()

    ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).delete_design_task(task_id)

    with session_factory() as session:
        assert session.get(task_model.DesignTask, task_id) is None
        assert session.get(design_model.DesignAttempt, design_attempt_id) is None
        assert session.get(design_model.ChallengeDesign, design_id) is None
        assert session.get(research_model.GenerationRequest, request_id) is not None


def test_delete_request_cascades_task_attempt_and_progress(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    attempt_id = uuid4()
    shard = f"{attempt_id}.json"
    progress = InMemoryProgressStore()
    progress.record(shard=shard, stage="build", status="failed")
    with session_factory() as session:
        task = session.get(task_model.DesignTask, task_id)
        assert task is not None
        request_id = task.generation_request_id
        with session.begin_nested():
            session.add(
                build_model.BuildAttempt(
                    id=attempt_id,
                    design_task_id=task_id,
                    attempt_no=1,
                    status="failed",
                    shard_basename=shard,
                )
            )
        session.commit()

    ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=progress,
    ).delete_generation_request(request_id)

    assert progress.events_for_shard(shard) == []
    with session_factory() as session:
        assert session.get(research_model.GenerationRequest, request_id) is None
        assert session.get(task_model.DesignTask, task_id) is None
        assert session.get(build_model.BuildAttempt, attempt_id) is None


def test_delete_latest_attempt_preserves_monotonic_allocator(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory, status="built")
    first_id = uuid4()
    latest_id = uuid4()
    with session_factory() as session:
        with session.begin():
            session.add_all(
                [
                    build_model.BuildAttempt(
                        id=first_id,
                        design_task_id=task_id,
                        attempt_no=1,
                        status="failed",
                        shard_basename=f"{first_id}.json",
                    ),
                    build_model.BuildAttempt(
                        id=latest_id,
                        design_task_id=task_id,
                        attempt_no=2,
                        status="succeeded",
                        shard_basename=f"{latest_id}.json",
                    ),
                ]
            )

    ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).delete_build_attempt(latest_id)

    with session_factory() as session:
        with session.begin():
            task = session.get(task_model.DesignTask, task_id)
            assert task is not None
            assert task.status == "build_failed"
            created = BuildAttemptsRepository(session).create_attempt(
                task_id, "attempt-3.json"
            )
            assert created.attempt_no == 3


def test_explicit_delete_keeps_artifact_referenced_by_surviving_attempt(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    target_id = uuid4()
    survivor_id = uuid4()
    artifact = paths.challenges / "web" / "web-0001-shared"
    artifact.mkdir(parents=True)
    stored = str(artifact.relative_to(tmp_path))
    with session_factory() as session:
        with session.begin():
            session.add_all(
                [
                    build_model.BuildAttempt(
                        id=target_id,
                        design_task_id=task_id,
                        attempt_no=1,
                        status="failed",
                        shard_basename=f"{target_id}.json",
                        resulting_challenge_dir=stored,
                    ),
                    build_model.BuildAttempt(
                        id=survivor_id,
                        design_task_id=task_id,
                        attempt_no=2,
                        status="failed",
                        shard_basename=f"{survivor_id}.json",
                        resulting_challenge_dir=stored,
                    ),
                ]
            )

    result = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).delete_build_attempt(target_id, delete_artifacts=True)

    assert artifact.exists()
    assert [(item.path, item.reason) for item in result.skipped] == [
        (str(artifact), "shared-reference")
    ]


def test_database_failure_rolls_back_progress_and_restores_queue_file(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    attempt_id = uuid4()
    shard = f"{attempt_id}.json"
    pending = paths.shards / "pending" / shard
    write_json(pending, {"build_attempt_id": str(attempt_id)})
    progress = PostgresProgressStore(session_factory)
    progress.record(shard=shard, stage="build", status="failed")
    with session_factory() as session:
        with session.begin():
            session.add(
                build_model.BuildAttempt(
                    id=attempt_id,
                    design_task_id=task_id,
                    attempt_no=1,
                    status="failed",
                    shard_basename=shard,
                )
            )

    service = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=progress,
    )

    def fail_delete_rows(*_args):
        raise RuntimeError("injected database failure")

    monkeypatch.setattr(service, "_delete_rows", fail_delete_rows)
    with pytest.raises(RuntimeError, match="injected database failure"):
        service.delete_build_attempt(attempt_id)

    assert pending.exists()
    assert progress.events_for_shard(shard)
    with session_factory() as session:
        assert session.get(build_model.BuildAttempt, attempt_id) is not None


def test_claim_race_restores_pending_file_and_keeps_row(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    attempt_id = uuid4()
    shard = f"{attempt_id}.json"
    pending = paths.shards / "pending" / shard
    write_json(pending, {"build_attempt_id": str(attempt_id)})
    with session_factory() as session:
        with session.begin():
            session.add(
                build_model.BuildAttempt(
                    id=attempt_id,
                    design_task_id=task_id,
                    attempt_no=1,
                    status="failed",
                    shard_basename=shard,
                )
            )

    service = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    )
    observations = iter((False, True))
    monkeypatch.setattr(service, "_running_matches", lambda _shard: next(observations))

    with pytest.raises(ResourceDeletionConflictError, match="claimed during deletion"):
        service.delete_build_attempt(attempt_id)

    assert pending.exists()
    with session_factory() as session:
        assert session.get(build_model.BuildAttempt, attempt_id) is not None


def test_running_research_and_design_are_authoritative_conflicts(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory, status="designing")
    design_attempt_id = uuid4()
    with session_factory() as session:
        task = session.get(task_model.DesignTask, task_id)
        assert task is not None
        request_id = task.generation_request_id
        run_id = task.research_run_id
        with session.begin_nested():
            session.add(
                design_model.DesignAttempt(
                    id=design_attempt_id,
                    design_task_id=task_id,
                    attempt=1,
                    status="running",
                    claim_token=uuid4(),
                    profile_name_used="default",
                )
            )
        session.commit()

    service = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    )
    with pytest.raises(ResourceDeletionConflictError, match="running design"):
        service.delete_design_task(task_id)

    with session_factory() as session:
        with session.begin():
            session.execute(
                sa.update(research_model.ResearchRun)
                .where(research_model.ResearchRun.id == run_id)
                .values(status="running")
            )
            session.execute(
                sa.update(design_model.DesignAttempt)
                .where(design_model.DesignAttempt.id == design_attempt_id)
                .values(status="failed")
            )

    with pytest.raises(ResourceDeletionConflictError, match="running research"):
        service.delete_generation_request(request_id)


def test_artifact_traversal_and_symlink_escape_are_refused(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    outside = tmp_path / "outside" / "artifact"
    outside.mkdir(parents=True)
    symlink = paths.challenges / "web" / "escaped-link"
    symlink.symlink_to(outside, target_is_directory=True)
    attempt_ids = [uuid4(), uuid4()]
    stored_paths = ["work/challenges/../outside", str(symlink.relative_to(tmp_path))]
    with session_factory() as session:
        with session.begin():
            for number, (attempt_id, stored) in enumerate(
                zip(attempt_ids, stored_paths, strict=True), start=1
            ):
                session.add(
                    build_model.BuildAttempt(
                        id=attempt_id,
                        design_task_id=task_id,
                        attempt_no=number,
                        status="failed",
                        shard_basename=f"{attempt_id}.json",
                        resulting_challenge_dir=stored,
                    )
                )

    service = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    )
    for attempt_id in attempt_ids:
        result = service.delete_build_attempt(attempt_id, delete_artifacts=True)
        assert [item.reason for item in result.skipped] == ["unsafe-path"]
    assert outside.exists()


def test_post_commit_cleanup_failure_is_reported_and_row_stays_deleted(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    attempt_id = uuid4()
    artifact = paths.challenges / "web" / "cleanup-warning"
    artifact.mkdir(parents=True)
    with session_factory() as session:
        with session.begin():
            session.add(
                build_model.BuildAttempt(
                    id=attempt_id,
                    design_task_id=task_id,
                    attempt_no=1,
                    status="failed",
                    shard_basename=f"{attempt_id}.json",
                    resulting_challenge_dir=str(artifact.relative_to(tmp_path)),
                )
            )

    monkeypatch.setattr(
        deletion_module,
        "_remove_path",
        lambda _path: (_ for _ in ()).throw(OSError("injected cleanup failure")),
    )
    result = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).delete_build_attempt(attempt_id, delete_artifacts=True)

    assert result.quarantined[0].reason == "cleanup-failed"
    assert "injected cleanup failure" in result.warnings[0]
    with session_factory() as session:
        assert session.get(build_model.BuildAttempt, attempt_id) is None


def test_recovery_treats_visible_source_planned_entry_as_not_moved(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=ROOT)
    paths.initialize()
    task_id = _seed_task(session_factory)
    operation = paths.work / "deletion-quarantine" / "planned-op"
    source = paths.shards / "pending" / "planned.json"
    destination = operation / "0000-planned.json"
    write_json(source, {"state": "visible"})
    write_json(
        operation / "manifest.json",
        {
            "root_resource": {"type": "design_task", "id": str(task_id)},
            "entries": [
                {
                    "source": str(source),
                    "destination": str(destination),
                    "state": "planned",
                }
            ],
        },
    )

    warnings = ResourceDeletionService(
        paths=paths,
        session_factory=session_factory,
        progress=InMemoryProgressStore(),
    ).recover_quarantine()

    assert warnings == []
    assert source.exists()
    assert not operation.exists()


def test_reference_table_lock_blocks_concurrent_reference_update(
    session_factory: SessionFactory,
):
    task_id = _seed_task(session_factory)
    attempt_id = uuid4()
    with session_factory() as session:
        with session.begin():
            session.add(
                build_model.BuildAttempt(
                    id=attempt_id,
                    design_task_id=task_id,
                    attempt_no=1,
                    status="failed",
                    shard_basename=f"{attempt_id}.json",
                )
            )

    owner = session_factory()
    contender = session_factory()
    try:
        owner.begin()
        ResourceDeletionService._lock_artifact_reference_tables(owner)
        contender.begin()
        contender.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(OperationalError):
            contender.execute(
                sa.update(build_model.BuildAttempt)
                .where(build_model.BuildAttempt.id == attempt_id)
                .values(resulting_challenge_dir="work/challenges/web/blocked")
            )
    finally:
        contender.rollback()
        owner.rollback()
        contender.close()
        owner.close()
