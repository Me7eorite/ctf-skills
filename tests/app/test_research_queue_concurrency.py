"""Postgres-backed queue claim concurrency tests."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from persistence.models import research as model
from persistence.session import SessionFactory
from services import ResearchJobService

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


def _seed_queue(session_factory: SessionFactory, count: int) -> None:
    service = ResearchJobService(session_factory)
    for index in range(count):
        service.submit_request("web", f"topic-{index}", 1, {"easy": 1})


def test_two_threads_claim_distinct_runs(session_factory: SessionFactory):
    _seed_queue(session_factory, 3)
    service = ResearchJobService(session_factory)

    def claim(agent_id: str):
        return service.claim_next_run(agent_id, 60)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ["w1", "w2"]))

    assert claimed[0] is not None
    assert claimed[1] is not None
    assert claimed[0].id != claimed[1].id
    with session_factory() as session:
        assert session.scalar(
            sa.select(sa.func.count()).where(model.ResearchRun.status == "queued")
        ) == 1


def test_ten_threads_claim_only_five_available_runs(session_factory: SessionFactory):
    _seed_queue(session_factory, 5)
    service = ResearchJobService(session_factory)

    def claim(index: int):
        return service.claim_next_run(f"w{index}", 60)

    with ThreadPoolExecutor(max_workers=10) as pool:
        claimed = list(pool.map(claim, range(10)))

    claimed_ids = [run.id for run in claimed if run is not None]
    empty_claims = [run for run in claimed if run is None]
    assert len(claimed_ids) == 5
    assert len(set(claimed_ids)) == 5
    assert len(empty_claims) == 5
