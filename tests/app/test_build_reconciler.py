"""PostgreSQL-backed build reconciler state-machine tests."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def test_queued_to_running_requires_claim_event(
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
    assert _row(session_factory, attempt_id).status == "queued"

    with session_factory() as session:
        session.add(
            ProgressEvent(
                shard=basename,
                challenge_id="",
                worker="hermes-01",
                stage="queued",
                status="running",
                percent=0,
                message="claimed",
            )
        )
        session.commit()
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

    reconciler.tick_once_sync()

    row = _row(session_factory, attempt_id)
    assert row.status == "lost"
    assert "disappeared" in row.error
    with session_factory() as session:
        assert session.get(task_model.DesignTask, task_id).status == "build_failed"


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
