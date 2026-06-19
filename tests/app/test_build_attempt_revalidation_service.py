"""PostgreSQL-backed tests for build-attempt revalidation."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from core.jsonio import write_json
from core.paths import ProjectPaths
from persistence.models import build_attempts as build_model
from persistence.models import challenge_designs as design_model
from persistence.models import design_tasks as task_model
from persistence.models import research as research_model
from persistence.models.progress import ProgressEvent, ProgressSnapshot
from persistence.repositories import BuildAttemptsRepository, PostgresProgressStore
from persistence.session import SessionFactory, transaction
from services.build_attempt_revalidation_service import (
    BuildAttemptRevalidationError,
    BuildAttemptRevalidationService,
)

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


class _PassingValidator:
    def validate_challenge(self, challenge_id: str) -> dict:
        return {"challenge_id": challenge_id, "status": "passed", "elapsed": 0.01}


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
        assert session.get(task_model.DesignTask, task_id).status == "build_failed"
    assert (paths.shards / "failed" / basename).exists()


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


def _write_web_challenge(paths: ProjectPaths, challenge_id: str) -> None:
    challenge = paths.challenges / "web" / f"{challenge_id}-demo"
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
