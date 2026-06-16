"""Postgres-backed lease recovery and heartbeat tests."""

from __future__ import annotations

import os
import subprocess
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

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


def _submit_and_claim(session_factory: SessionFactory):
    service = ResearchJobService(session_factory)
    _request, _run = service.submit_request("web", "lease", 1, {"easy": 1})
    claimed = service.claim_next_run("w1", 60)
    assert claimed is not None
    assert claimed.claim_token is not None
    assert claimed.lease_expires_at is not None
    return service, claimed


def test_expired_lease_creates_retry_and_new_claim(
    session_factory: SessionFactory,
):
    service, claimed = _submit_and_claim(session_factory)
    old_token = claimed.claim_token

    with session_factory() as session:
        row = session.get(model.ResearchRun, claimed.id)
        assert row is not None
        row.lease_expires_at = sa.func.now() - sa.text("interval '1 minute'")
        session.commit()

    recovered = service.claim_next_run("w2", 60)
    assert recovered is not None
    assert recovered.parent_run_id == claimed.id
    assert recovered.claim_token is not None
    assert recovered.claim_token != old_token

    with session_factory() as session:
        expired = session.get(model.ResearchRun, claimed.id)
        assert expired is not None
        assert expired.status == "failed"
        assert expired.parent_run_id is None
        assert expired.last_error == "lease expired"
        assert expired.claim_token == old_token


def test_heartbeat_rejects_wrong_owner_token_and_terminal_rows(
    session_factory: SessionFactory,
):
    service, claimed = _submit_and_claim(session_factory)
    assert claimed.claim_token is not None

    with session_factory() as session:
        row = session.get(model.ResearchRun, claimed.id)
        assert row is not None
        before_claimed_by = row.claimed_by
        before_lease = row.lease_expires_at

    assert service.heartbeat(claimed.id, "wrong", claimed.claim_token, 900) is False
    with session_factory() as session:
        row = session.get(model.ResearchRun, claimed.id)
        assert row is not None
        assert row.claimed_by == before_claimed_by
        assert row.lease_expires_at == before_lease

    assert service.heartbeat(claimed.id, "w1", uuid4(), 900) is False
    with session_factory() as session:
        row = session.get(model.ResearchRun, claimed.id)
        assert row is not None
        assert row.claimed_by == before_claimed_by
        assert row.lease_expires_at == before_lease
        row.status = "completed"
        session.commit()

    assert service.heartbeat(claimed.id, "w1", claimed.claim_token, 900) is False
    with session_factory() as session:
        row = session.get(model.ResearchRun, claimed.id)
        assert row is not None
        assert row.claimed_by == before_claimed_by
        assert row.lease_expires_at == before_lease


def test_heartbeat_advances_lease_for_current_claim(
    session_factory: SessionFactory,
):
    service, claimed = _submit_and_claim(session_factory)
    assert claimed.claim_token is not None
    assert claimed.lease_expires_at is not None
    before_lease = claimed.lease_expires_at

    assert service.heartbeat(claimed.id, "w1", claimed.claim_token, 900) is True

    with session_factory() as session:
        row = session.get(model.ResearchRun, claimed.id)
        assert row is not None
        assert row.lease_expires_at is not None
        assert row.lease_expires_at > before_lease + timedelta(seconds=800)
