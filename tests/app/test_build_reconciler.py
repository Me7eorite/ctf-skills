"""PostgreSQL-backed build reconciler state-machine tests."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from core.jsonio import write_json
from core.paths import ProjectPaths
from persistence.errors import PersistenceConnectionError
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models import research as research_model
from persistence.models.progress import ProgressEvent, ProgressSnapshot
from persistence.session import SessionFactory
from services.build_reconciler import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    BuildReconciler,
    _poll_interval_from_env,
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
    _clean(session_factory)
    yield
    _clean(session_factory)


def _clean(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        session.execute(sa.delete(ProgressSnapshot))
        session.execute(sa.delete(ProgressEvent))
        session.execute(sa.delete(build_model.BuildAttempt))
        session.execute(sa.delete(task_model.DesignTask))
        session.execute(sa.delete(research_model.ResearchRun))
        session.execute(sa.delete(research_model.GenerationRequest))
        session.commit()


def _backdate_past_grace(
    session_factory: SessionFactory, attempt_id: UUID
) -> None:
    """Move the attempt's created_at outside the lost-marking grace window.

    Grace was bumped to 300s in Phase 0 hot-fix; backdate to 10 minutes so the
    test is robust to any future tweak as long as the window stays < 10min.
    """
    with session_factory() as session:
        session.get(build_model.BuildAttempt, attempt_id).created_at = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        )
        session.commit()


def _seed_attempt(session_factory: SessionFactory) -> tuple[UUID, UUID, str]:
    with session_factory() as session:
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
            title="Reconcile task",
            category="web",
            difficulty="easy",
            primary_technique="test",
            learning_objective="test",
            points=100,
            port=8081,
            scenario="",
            constraints={},
            evidence_summary="",
            finding_ids=[],
            status="building",
        )
        attempt_id = uuid4()
        basename = f"{attempt_id}.json"
        attempt = build_model.BuildAttempt(
            id=attempt_id,
            design_task_id=task.id,
            attempt_no=1,
            status="queued",
            shard_basename=basename,
        )
        session.add_all([request, run, task, attempt])
        session.commit()
        return task.id, attempt_id, basename


def _payload(task_id: UUID, attempt_id: UUID, challenge_id: str) -> dict:
    return {
        "build_attempt_id": str(attempt_id),
        "design_task_id": str(task_id),
        "challenges": [
            {"id": challenge_id, "category": "web", "design": {}}
        ],
    }


def _reconciler(
    tmp_path: Path,
    session_factory: SessionFactory,
) -> BuildReconciler:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    return BuildReconciler(
        paths=paths,
        session_factory=session_factory,
        poll_interval_seconds=1,
    )


def _challenge_id(session_factory: SessionFactory, task_id: UUID) -> str:
    with session_factory() as session:
        return session.get(task_model.DesignTask, task_id).challenge_id


def _row(session_factory: SessionFactory, attempt_id: UUID):
    with session_factory() as session:
        row = session.get(build_model.BuildAttempt, attempt_id)
        session.expunge(row)
        return row


def test_queued_to_running_uses_running_shard_claim_sidecar(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id, attempt_id, basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    running = reconciler.paths.shards / "running" / f"{attempt_id}.hermes-01.json"
    write_json(running, _payload(task_id, attempt_id, _challenge_id(session_factory, task_id)))
    write_json(
        running.with_suffix(".json.claim.json"),
        {
            "source_name": basename,
            "worker": "hermes-01",
            "claimed_at": "2026-06-18T10:00:00Z",
        },
    )

    reconciler.tick_once_sync()

    row = _row(session_factory, attempt_id)
    assert row.status == "running"
    assert row.worker == "hermes-01"
    assert row.started_at == datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)


def test_dry_run_requeue_stays_queued(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id, attempt_id, basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    write_json(
        reconciler.paths.shards / "pending" / basename,
        _payload(task_id, attempt_id, _challenge_id(session_factory, task_id)),
    )

    reconciler.tick_once_sync()

    assert _row(session_factory, attempt_id).status == "queued"


def test_aborted_sequential_result_json_does_not_mark_attempt_failed(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id, attempt_id, basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    write_json(
        reconciler.paths.logs / "dashboard-sequential-worker-result.json",
        {
            "abort_reason": "consecutive_infra",
            "aborted": [str(attempt_id)],
            "outcomes": [
                {
                    "status": "aborted",
                    "shard": str(attempt_id),
                    "abort_reason": "consecutive_infra",
                }
            ],
        },
    )
    write_json(
        reconciler.paths.shards / "pending" / basename,
        _payload(task_id, attempt_id, _challenge_id(session_factory, task_id)),
    )

    reconciler.tick_once_sync()

    row = _row(session_factory, attempt_id)
    assert row.status == "queued"
    assert row.error is None


def test_fast_success_and_artifact_availability_do_not_rewrite_status(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id, attempt_id, basename = _seed_attempt(session_factory)
    challenge_id = _challenge_id(session_factory, task_id)
    reconciler = _reconciler(tmp_path, session_factory)
    done = reconciler.paths.shards / "done" / basename
    write_json(done, _payload(task_id, attempt_id, challenge_id))
    write_json(
        done.with_suffix(".json.claim.json"),
        {
            "source_name": basename,
            "worker": "hermes-fast",
            "claimed_at": "2026-06-18T11:00:00Z",
        },
    )
    metadata = (
        reconciler.paths.challenges / "web" / f"{challenge_id}-demo" / "metadata.json"
    )
    write_json(metadata, {"id": challenge_id, "solve_status": "passed"})

    reconciler.tick_once_sync()
    succeeded = _row(session_factory, attempt_id)
    assert succeeded.status == "succeeded"
    assert succeeded.artifact_status == "present"
    assert succeeded.worker == "hermes-fast"
    assert succeeded.resulting_challenge_dir.endswith(f"{challenge_id}-demo")
    with session_factory() as session:
        assert session.get(task_model.DesignTask, task_id).status == "built"

    metadata.unlink()
    reconciler.tick_once_sync()
    missing = _row(session_factory, attempt_id)
    assert missing.status == "succeeded"
    assert missing.artifact_status == "missing"
    with session_factory() as session:
        assert session.get(task_model.DesignTask, task_id).status == "built"

    write_json(metadata, {"id": challenge_id, "solve_status": "passed"})
    reconciler.tick_once_sync()
    assert _row(session_factory, attempt_id).artifact_status == "present"


@pytest.mark.parametrize(
    "state,with_artifact,error_fragment",
    [
        ("failed", False, "execution failed"),
        ("done", False, "directory missing"),
        ("done", True, "solve_status"),
    ],
)
def test_terminal_failure_paths(
    state: str,
    with_artifact: bool,
    error_fragment: str,
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id, attempt_id, basename = _seed_attempt(session_factory)
    challenge_id = _challenge_id(session_factory, task_id)
    reconciler = _reconciler(tmp_path, session_factory)
    write_json(
        reconciler.paths.shards / state / basename,
        _payload(task_id, attempt_id, challenge_id),
    )
    if with_artifact:
        write_json(
            reconciler.paths.challenges
            / "web"
            / f"{challenge_id}-demo"
            / "metadata.json",
            {"id": challenge_id, "solve_status": "failed"},
        )

    reconciler.tick_once_sync()

    row = _row(session_factory, attempt_id)
    assert row.status == "failed"
    assert error_fragment in row.error
    assert row.finished_at is not None
    with session_factory() as session:
        assert session.get(task_model.DesignTask, task_id).status == "build_failed"


def test_vanished_active_shard_becomes_lost(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id, attempt_id, _basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)

    _backdate_past_grace(session_factory, attempt_id)

    reconciler.tick_once_sync()

    row = _row(session_factory, attempt_id)
    assert row.status == "lost"
    assert "disappeared" in row.error
    with session_factory() as session:
        assert session.get(task_model.DesignTask, task_id).status == "build_failed"


def test_fresh_attempt_within_grace_window_is_not_lost(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    """A brand-new row whose shard is not yet on disk stays queued."""
    _task_id, attempt_id, _basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)

    reconciler.tick_once_sync()

    assert _row(session_factory, attempt_id).status == "queued"


def test_attempt_committed_after_scan_boundary_is_not_false_lost(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    _task_id, attempt_id, _basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    with session_factory() as session:
        session.get(build_model.BuildAttempt, attempt_id).created_at = (
            datetime.now(timezone.utc) + timedelta(minutes=1)
        )
        session.commit()

    reconciler.tick_once_sync()

    assert _row(session_factory, attempt_id).status == "queued"


def test_failed_staging_publication_prevents_false_lost(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    task_id, attempt_id, basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    staged = reconciler.paths.build_attempt_staging / f"{attempt_id}.json"
    write_json(staged, _payload(task_id, attempt_id, _challenge_id(session_factory, task_id)))
    monkeypatch.setattr(
        reconciler.orchestration,
        "_publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk busy")),
    )

    reconciler.tick_once_sync()

    assert staged.exists()
    assert _row(session_factory, attempt_id).status == "queued"
    assert not (reconciler.paths.shards / "pending" / basename).exists()


def test_unattributed_basename_collision_is_ignored(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    _task_id, attempt_id, basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    write_json(
        reconciler.paths.shards / "done" / basename,
        {"challenges": [{"id": "web-unattributed", "category": "web"}]},
    )
    _backdate_past_grace(session_factory, attempt_id)

    reconciler.tick_once_sync()

    assert _row(session_factory, attempt_id).status == "lost"


def test_mismatched_design_task_attribution_is_ignored(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    task_id, attempt_id, basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    write_json(
        reconciler.paths.shards / "pending" / basename,
        _payload(uuid4(), attempt_id, _challenge_id(session_factory, task_id)),
    )
    _backdate_past_grace(session_factory, attempt_id)

    reconciler.tick_once_sync()

    assert _row(session_factory, attempt_id).status == "lost"


def test_poll_interval_configuration(monkeypatch: pytest.MonkeyPatch, caplog):
    monkeypatch.setenv("BUILD_RECONCILER_POLL_SECONDS", "12")
    with caplog.at_level("WARNING"):
        assert _poll_interval_from_env() == 12
    assert "BUILD_RECONCILER_POLL_SECONDS=12" in caplog.text
    caplog.clear()
    monkeypatch.setenv("BUILD_RECONCILER_POLL_SECONDS", "0")
    with caplog.at_level("WARNING"):
        assert _poll_interval_from_env() == DEFAULT_POLL_INTERVAL_SECONDS
    assert "using 5" in caplog.text
    caplog.clear()
    monkeypatch.delenv("BUILD_RECONCILER_POLL_SECONDS")
    with caplog.at_level("WARNING"):
        assert _poll_interval_from_env() == DEFAULT_POLL_INTERVAL_SECONDS
    assert "unset" in caplog.text


def test_run_forever_survives_persistence_failure(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
):
    reconciler = _reconciler(tmp_path, session_factory)
    calls = 0

    def fail_tick():
        nonlocal calls
        calls += 1
        raise PersistenceConnectionError("postgres unavailable")

    def stop_sleep(_seconds):
        reconciler.stop()

    monkeypatch.setattr(reconciler, "tick_once_sync", fail_tick)
    monkeypatch.setattr("services.build_reconciler.time.sleep", stop_sleep)
    with caplog.at_level("WARNING"):
        reconciler.run_forever()

    assert calls == 1
    assert "postgres unavailable" in caplog.text


# ============================================================================
# Phase 0 hot fixes — lost-race remediation
# ============================================================================


def test_grace_window_is_300_seconds_not_60(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    """Regression: production saw attempts marked lost at 61-65s after creation
    while their shards were still in pending/. New grace gives the worker
    enough time to claim+heartbeat before reconciler decides they're gone.
    """
    _task_id, attempt_id, _basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    with session_factory() as session:
        session.get(build_model.BuildAttempt, attempt_id).created_at = (
            datetime.now(timezone.utc) - timedelta(seconds=120)
        )
        session.commit()

    reconciler.tick_once_sync()

    # 120s is well within the 300s grace window; status must remain queued.
    assert _row(session_factory, attempt_id).status == "queued"


def test_rescan_retry_prevents_false_lost_on_transient_glob_miss(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    """The first scan misses the shard (simulating a glob-iteration snapshot
    artifact during a worker mv); the rescan inside _rescan_still_disappeared
    sees it; the row must NOT be marked lost.

    To isolate the rescan logic from `_payload_present_for_row` (which also
    scans the filesystem), this test mocks both: _payload_present_for_row
    returns False (as it would during the same race), but the second call to
    _scan_attributed_shards finds the shard.
    """
    task_id, attempt_id, basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    _backdate_past_grace(session_factory, attempt_id)

    original_scan = reconciler._scan_attributed_shards
    call_count = {"n": 0}
    # Stage the shard in pending so the SECOND scan can see it via the
    # original scan logic. Force _payload_present_for_row to mirror the
    # transient miss so the test exercises the rescan path.
    pending = reconciler.paths.shards / "pending" / basename
    pending.parent.mkdir(parents=True, exist_ok=True)
    write_json(pending, _payload(task_id, attempt_id, _challenge_id(session_factory, task_id)))
    monkeypatch.setattr(reconciler, "_payload_present_for_row", lambda _row: False)

    def flaky_scan() -> dict[UUID, Any]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {}  # transient miss
        return original_scan()

    monkeypatch.setattr(reconciler, "_scan_attributed_shards", flaky_scan)

    reconciler.tick_once_sync()

    assert call_count["n"] >= 2, "rescan must run after first miss"
    assert _row(session_factory, attempt_id).status == "queued"


def test_persistent_disappearance_still_marks_lost_after_rescan(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    """If both first scan AND rescan see nothing, the lost decision still
    fires (rescan is for transient miss only, not a free pardon)."""
    _task_id, attempt_id, _basename = _seed_attempt(session_factory)
    reconciler = _reconciler(tmp_path, session_factory)
    _backdate_past_grace(session_factory, attempt_id)

    reconciler.tick_once_sync()

    assert _row(session_factory, attempt_id).status == "lost"
