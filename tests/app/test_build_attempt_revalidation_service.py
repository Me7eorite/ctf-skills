"""PostgreSQL-backed tests for build-attempt revalidation."""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

import services.build_attempt_revalidation_service as revalidation_module
from core.jsonio import write_json
from core.paths import ProjectPaths
from persistence.models import build_attempts as build_model
from persistence.models import challenge_designs as design_model
from persistence.models import design_tasks as task_model
from persistence.models import executions as exec_model
from persistence.models import research as research_model
from persistence.models.progress import ProgressEvent, ProgressSnapshot
from persistence.repositories import (
    BuildAttemptsRepository,
    ExecutionsRepository,
    PostgresProgressStore,
)
from persistence.session import SessionFactory, transaction
from services.build_attempt_revalidation_service import (
    BuildAttemptRevalidationError,
    BuildAttemptRevalidationService,
)
from services.build_attempt_repair_service import BuildAttemptRepairService
from services.build_attempt_repair_service import _challenge_directory

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


class _PassingValidator:
    def validate_one(self, challenge_dir: Path) -> dict:
        return {"path": str(challenge_dir), "status": "passed", "elapsed": 0.01}


class _RaisingValidator:
    def validate_one(self, challenge_dir: Path) -> dict:
        raise RuntimeError(f"validator crashed for {challenge_dir.name}")


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
    yield


def test_revalidate_repairs_failed_attempt_without_creating_sibling(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(
        session_factory
    )
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)
    _write_web_challenge(paths, challenge_id)

    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_PassingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )

    service.revalidate(attempt_id)

    with session_factory() as session:
        attempts = session.scalars(
            sa.select(build_model.BuildAttempt).where(
                build_model.BuildAttempt.design_task_id == task_id
            )
        ).all()
        assert len(attempts) == 1
        row = attempts[0]
        assert row.id == attempt_id
        assert row.status == "succeeded"
        assert row.artifact_status == "present"
        assert row.error is None
        assert row.resulting_challenge_dir.endswith(f"{challenge_id}-demo")
        assert session.get(task_model.DesignTask, task_id).status == "built"
        events = session.scalars(
            sa.select(ProgressEvent).where(ProgressEvent.shard == basename)
        ).all()
        assert ("validate", "running") in {
            (event.stage, event.status) for event in events
        }
        assert ("validate", "passed") in {
            (event.stage, event.status) for event in events
        }
        assert ("complete", "passed") in {
            (event.stage, event.status) for event in events
        }

    assert not (paths.shards / "failed" / basename).exists()
    assert (paths.shards / "done" / basename).exists()


def test_revalidate_missing_challenge_keeps_attempt_failed(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(
        session_factory
    )
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)
    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_PassingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )

    with pytest.raises(BuildAttemptRevalidationError, match="missing_challenge"):
        service.revalidate(attempt_id)

    with session_factory() as session:
        row = session.get(build_model.BuildAttempt, attempt_id)
        assert row.status == "failed"
    assert "missing_challenge" in row.error


def test_revalidate_uses_execution_workspace_when_global_challenge_missing(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(session_factory)
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)

    workspace = paths.executions / str(attempt_id) / "current" / "output" / "challenges"
    _write_web_challenge(paths, challenge_id, root=workspace)
    global_challenge = paths.challenges / "web" / f"{challenge_id}-demo"
    if global_challenge.exists():
        import shutil
        shutil.rmtree(global_challenge)

    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_PassingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )

    service.revalidate(attempt_id)

    with session_factory() as session:
        row = session.get(build_model.BuildAttempt, attempt_id)
        assert row is not None
        assert row.status == "succeeded"


def test_revalidate_rejects_stale_failed_attempt(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(
        session_factory
    )
    with transaction(factory=session_factory) as session:
        repo = BuildAttemptsRepository(session)
        second_id = uuid4()
        second = repo.create_attempt(task_id, f"{second_id}.json", attempt_id=second_id)
        repo.update_to_terminal(second.id, status="failed", error="newer failure")
        session.get(task_model.DesignTask, task_id).status = "build_failed"
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)

    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_PassingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )

    with pytest.raises(BuildAttemptRevalidationError, match="latest"):
        service.revalidate(attempt_id)


def test_revalidate_rejects_current_execution(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(
        session_factory
    )
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)
    _write_web_challenge(paths, challenge_id)
    with transaction(factory=session_factory) as session:
        parent_id = _seed_terminal_execution(session, attempt_id)
        repo = ExecutionsRepository(session)
        execution = repo.schedule_execution(
            attempt_id,
            execution_kind="retry",
            parent_execution_id=parent_id,
        )
        repo.claim_queued(attempt_id, worker_id="w", lease_ttl_seconds=300)
        container = session.get(build_model.BuildAttempt, attempt_id)
        container.status = "failed"
        assert container.current_execution_id == execution.id

    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_PassingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )

    with pytest.raises(BuildAttemptRevalidationError, match="current"):
        service.revalidate(attempt_id)


def test_revalidate_rejects_nonterminal_latest_execution(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(
        session_factory
    )
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)
    _write_web_challenge(paths, challenge_id)
    with transaction(factory=session_factory) as session:
        parent_id = _seed_terminal_execution(session, attempt_id)
        ExecutionsRepository(session).schedule_execution(
            attempt_id,
            execution_kind="retry",
            parent_execution_id=parent_id,
        )
        container = session.get(build_model.BuildAttempt, attempt_id)
        container.status = "failed"
        container.current_execution_id = None

    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_PassingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )

    with pytest.raises(BuildAttemptRevalidationError, match="terminal"):
        service.revalidate(attempt_id)


def test_revalidate_rejects_duplicate_advisory_lock_before_progress(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(
        session_factory
    )
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)
    key = attempt_id.int & ((1 << 63) - 1)
    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_PassingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )

    with session_factory.engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.pg_try_advisory_lock(key)))
        connection.commit()
        try:
            with pytest.raises(BuildAttemptRevalidationError, match="already"):
                service.revalidate(attempt_id)
        finally:
            connection.execute(sa.select(sa.func.pg_advisory_unlock(key)))
            connection.commit()

    with session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ProgressEvent)) == 0


def test_validator_exception_writes_terminal_failure_events(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(
        session_factory
    )
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)
    _write_web_challenge(paths, challenge_id)
    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_RaisingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )

    with pytest.raises(BuildAttemptRevalidationError, match="validator_error"):
        service.revalidate(attempt_id)

    with session_factory() as session:
        row = session.get(build_model.BuildAttempt, attempt_id)
        events = session.scalars(
            sa.select(ProgressEvent)
            .where(ProgressEvent.shard == basename)
            .order_by(ProgressEvent.id)
        ).all()
        assert "validator_error" in row.error
        assert [(event.stage, event.status) for event in events][-2:] == [
            ("validate", "failed"),
            ("complete", "failed"),
        ]


def test_recorded_artifact_directory_wins_over_ambiguous_lookup(
    tmp_path: Path,
    session_factory: SessionFactory,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(
        session_factory
    )
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)
    _write_web_challenge(paths, challenge_id)
    selected = paths.challenges / "web" / f"{challenge_id}-demo"
    duplicate = paths.challenges / "web" / f"{challenge_id}-duplicate"
    duplicate.mkdir(parents=True)
    write_json(duplicate / "metadata.json", {"id": challenge_id})
    with transaction(factory=session_factory) as session:
        session.get(build_model.BuildAttempt, attempt_id).resulting_challenge_dir = (
            selected.relative_to(paths.root).as_posix()
        )
    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_PassingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )

    service.revalidate(attempt_id)

    with session_factory() as session:
        assert session.get(build_model.BuildAttempt, attempt_id).status == "succeeded"


def test_database_failure_after_shard_move_restores_failed_queue(
    tmp_path: Path,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    task_id, attempt_id, basename, challenge_id = _seed_failed_attempt(
        session_factory
    )
    _write_failed_shard(paths, task_id, attempt_id, basename, challenge_id)
    real_transaction = revalidation_module.transaction

    @contextmanager
    def failing_transaction(*, factory=None):
        with real_transaction(factory=factory) as session:
            yield session
            raise RuntimeError("simulated commit failure")

    service = BuildAttemptRevalidationService(
        paths=paths,
        progress=PostgresProgressStore(session_factory),
        session_factory=session_factory,
        validator=_PassingValidator(),  # type: ignore[arg-type]
        image_exists=lambda _image: True,
    )
    monkeypatch.setattr(revalidation_module, "transaction", failing_transaction)

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        service._mark_succeeded(
            attempt_id,
            shard_basename=basename,
            challenge_dir=f"work/challenges/web/{challenge_id}-demo",
        )

    assert (paths.shards / "failed" / basename).is_file()
    assert not (paths.shards / "done" / basename).exists()


def _seed_failed_attempt(
    session_factory: SessionFactory,
) -> tuple[UUID, UUID, str, str]:
    with transaction(factory=session_factory) as session:
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
            title="Revalidation task",
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
        session.add_all([request, run, task])
        attempt_id = uuid4()
        repo = BuildAttemptsRepository(session)
        attempt = repo.create_attempt(
            task.id,
            f"{attempt_id}.json",
            attempt_id=attempt_id,
        )
        repo.update_to_terminal(
            attempt.id,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            resulting_challenge_dir=None,
            artifact_status="missing",
            error="shard execution failed",
        )
        task.status = "build_failed"
        return task.id, attempt.id, attempt.shard_basename, task.challenge_id


def _seed_terminal_execution(session, attempt_id: UUID) -> UUID:
    execution_id = uuid4()
    session.add(
        exec_model.Execution(
            id=execution_id,
            build_attempt_id=attempt_id,
            iteration_no=1,
            execution_kind="initial",
            execution_mode="standard",
            status="failed",
            error="initial failed",
        )
    )
    session.flush()
    return execution_id


def _write_failed_shard(
    paths: ProjectPaths,
    task_id: UUID,
    attempt_id: UUID,
    basename: str,
    challenge_id: str,
) -> None:
    write_json(
        paths.shards / "failed" / basename,
        {
            "build_attempt_id": str(attempt_id),
            "design_task_id": str(task_id),
            "challenges": [{"id": challenge_id, "category": "web"}],
        },
    )


def _write_web_challenge(paths: ProjectPaths, challenge_id: str, *, root: Path | None = None) -> Path:
    base = root or paths.challenges
    challenge = base / "web" / f"{challenge_id}-demo"
    write_json(
        challenge / "metadata.json",
        {
            "id": challenge_id,
            "title": "Demo",
            "difficulty": "easy",
            "category": "web",
            "flag": "flag{demo}",
            "build_status": "passed",
            "build_command": "docker compose build",
            "docker_image": "ctf/demo:latest",
            "runtime": "python",
            "framework": "flask",
        },
    )
    (challenge / "deploy" / "src").mkdir(parents=True, exist_ok=True)
    (challenge / "deploy" / "src" / "app.py").write_text(
        "print('ok')\n",
        encoding="utf-8",
    )
    (challenge / "deploy" / "Dockerfile").write_text(
        "FROM python:3.11-slim\n",
        encoding="utf-8",
    )
    (challenge / "deploy" / "docker-compose.yml").write_text(
        "services:\n  app:\n    build: .\n",
        encoding="utf-8",
    )
    (challenge / "validate.sh").write_text(
        "#!/bin/sh\necho flag{demo}\n",
        encoding="utf-8",
    )
    (challenge / "writenup").mkdir(exist_ok=True)
    (challenge / "writenup" / "exp.py").write_text(
        "print('flag{demo}')\n",
        encoding="utf-8",
    )
    body = "## Overview\n" + ("A" * 520) + "\n## Solve\n" + ("B" * 80)
    (challenge / "writenup" / "wp.md").write_text(body, encoding="utf-8")
    (challenge / "README.md").write_text(body, encoding="utf-8")
    return challenge


def test_repair_challenge_directory_prefers_execution_workspace_when_global_missing(
    tmp_path: Path,
):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    workspace = paths.executions / "attempt-1" / "current" / "output" / "challenges"
    challenge = _write_web_challenge(paths, "web-1234", root=workspace)
    found = _challenge_directory(paths, "web-1234", None)
    assert found == challenge
