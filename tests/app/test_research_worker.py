"""Postgres-backed tests for ResearchWorker."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from core.paths import ProjectPaths
from persistence.models import research as model
from persistence.session import SessionFactory
from services import ResearchJobService
from services.research_worker import ResearchWorker

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def session_factory() -> SessionFactory:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        yield SessionFactory(engine)
    finally:
        engine.dispose()
        subprocess.run(["uv", "run", "alembic", "downgrade", "base"], cwd=ROOT, env=env, check=False)


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory):
    with session_factory() as session:
        session.execute(sa.delete(model.ResearchFindingSource))
        session.execute(sa.delete(model.ResearchFinding))
        session.execute(sa.delete(model.ResearchSource))
        session.execute(sa.delete(model.HermesProfileBinding))
        session.execute(sa.delete(model.ResearchRun))
        session.execute(sa.delete(model.GenerationRequest))
        session.add(
            model.HermesProfileBinding(
                role="research",
                profile_name="default",
                description="default binding",
                status="enabled",
            )
        )
        session.commit()
    yield


@pytest.fixture
def paths(tmp_path: Path) -> ProjectPaths:
    project_paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    project_paths.initialize()
    return project_paths


class RecordingExecutor:
    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.seen_run_ids = []
        self._lock = threading.Lock()

    def execute(self, research_run, _agent_id, _lease_seconds, _hermes_timeout_seconds):
        with self._lock:
            self.seen_run_ids.append(research_run.id)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)


class InterruptAfterClaimService:
    def __init__(self, inner: ResearchJobService) -> None:
        self.inner = inner
        self.claimed_run = None

    def claim_next_run(self, agent_id: str, lease_seconds: int, **kwargs):
        self.claimed_run = self.inner.claim_next_run(agent_id, lease_seconds, **kwargs)
        raise KeyboardInterrupt


def _seed_requests(session_factory: SessionFactory, count: int) -> None:
    service = ResearchJobService(session_factory)
    for index in range(count):
        service.submit_request("web", f"topic-{index}", 1, {"easy": 1})


def test_worker_processes_max_jobs_against_database(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    _seed_requests(session_factory, 5)
    executor = RecordingExecutor()
    worker = ResearchWorker(
        paths,
        ResearchJobService(session_factory),
        executor,
    )

    result = worker.run(
        "w1",
        loop=True,
        max_jobs=3,
        poll_interval_seconds=0.01,
        lease_seconds=60,
        hermes_timeout_seconds=30,
    )

    assert result == {"processed": 3, "agent_id": "w1"}
    assert len(executor.seen_run_ids) == 3
    with session_factory() as session:
        running_count = session.scalar(
            sa.select(sa.func.count()).where(
                model.ResearchRun.status == "running",
                model.ResearchRun.claimed_by == "w1",
            )
        )
        queued_count = session.scalar(
            sa.select(sa.func.count()).where(model.ResearchRun.status == "queued")
        )
        assert running_count == 3
        assert queued_count == 2


def test_two_workers_claim_distinct_runs(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    _seed_requests(session_factory, 3)
    executor_a = RecordingExecutor(delay_seconds=0.02)
    executor_b = RecordingExecutor(delay_seconds=0.02)
    worker_a = ResearchWorker(paths, ResearchJobService(session_factory), executor_a)
    worker_b = ResearchWorker(paths, ResearchJobService(session_factory), executor_b)

    def run_worker(worker: ResearchWorker, agent_id: str):
        return worker.run(
            agent_id,
            loop=False,
            max_jobs=3,
            poll_interval_seconds=0.01,
            lease_seconds=60,
            hermes_timeout_seconds=30,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: run_worker(*args),
                [(worker_a, "w1"), (worker_b, "w2")],
            )
        )

    seen_run_ids = executor_a.seen_run_ids + executor_b.seen_run_ids
    assert sum(result["processed"] for result in results) == 3
    assert len(seen_run_ids) == 3
    assert len(set(seen_run_ids)) == 3
    with session_factory() as session:
        assert session.scalar(
            sa.select(sa.func.count()).where(model.ResearchRun.status == "queued")
        ) == 0


def test_keyboard_interrupt_after_claim_before_return_leaves_recoverable_running_row(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    _seed_requests(session_factory, 1)
    service = InterruptAfterClaimService(ResearchJobService(session_factory))
    executor = RecordingExecutor()
    worker = ResearchWorker(paths, service, executor)

    result = worker.run(
        "w1",
        loop=True,
        max_jobs=1,
        poll_interval_seconds=0.01,
        lease_seconds=60,
        hermes_timeout_seconds=30,
    )

    assert result == {"processed": 0, "agent_id": "w1", "interrupted": True}
    assert executor.seen_run_ids == []
    assert service.claimed_run is not None
    with session_factory() as session:
        run = session.get(model.ResearchRun, service.claimed_run.id)
        assert run is not None
        assert run.status == "running"
        assert run.claimed_by == "w1"
        assert run.claim_token is not None
        assert run.lease_expires_at is not None


def test_keyboard_interrupt_during_execute_marks_current_run_failed(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    _seed_requests(session_factory, 1)
    service = ResearchJobService(session_factory)

    class InterruptingExecutor:
        def execute(self, *_args):
            raise KeyboardInterrupt

    worker = ResearchWorker(paths, service, InterruptingExecutor())

    result = worker.run(
        "w1",
        loop=True,
        max_jobs=1,
        poll_interval_seconds=0.01,
        lease_seconds=60,
        hermes_timeout_seconds=30,
    )

    assert result == {"processed": 0, "agent_id": "w1", "interrupted": True}
    with session_factory() as session:
        rows = session.scalars(
            sa.select(model.ResearchRun).order_by(model.ResearchRun.attempt)
        ).all()
        assert rows[0].status == "failed"
        assert rows[0].last_error == "cancelled by operator"
        assert rows[1].status == "queued"
