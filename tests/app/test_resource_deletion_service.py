"""PostgreSQL service tests for resource deletion."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from core.jsonio import write_json
from core.paths import ProjectPaths
from core.state import InMemoryProgressStore
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
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
