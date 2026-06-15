"""Postgres-backed tests for research repository and queue primitives."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from domain.research_validators import ResearchValidationError
from persistence.models import research as model
from persistence.repositories import ResearchRepository
from persistence.session import SessionFactory
from services import ResearchAttemptError, ResearchJobService, StaleClaimError

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


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory):
    with session_factory() as session:
        session.execute(sa.delete(model.ResearchFindingSource))
        session.execute(sa.delete(model.ResearchFinding))
        session.execute(sa.delete(model.ResearchSource))
        session.execute(sa.delete(model.HermesProfileBinding))
        session.execute(sa.delete(model.ResearchRun))
        session.execute(sa.delete(model.GenerationRequest))
        session.execute(
            sa.delete(model.ChallengeCategory).where(
                model.ChallengeCategory.code.not_in(["web", "pwn", "re"])
            )
        )
        session.add(
            model.HermesProfileBinding(
                role="research",
                profile_name="default",
                description="默认绑定，operator 可改",
                status="enabled",
            )
        )
        session.commit()
    yield


def _repo(session_factory: SessionFactory) -> ResearchRepository:
    return ResearchRepository(session_factory())


def _create_request_and_run(session_factory: SessionFactory, *, max_attempts: int = 3):
    service = ResearchJobService(session_factory)
    return service.submit_request(
        "web",
        "SQL injection",
        2,
        {"easy": 1, "medium": 1},
        seed_urls=("https://example.com/sqli",),
        max_attempts=max_attempts,
    )


def test_categories_and_generation_request_round_trip(session_factory: SessionFactory):
    session = session_factory()
    try:
        repo = ResearchRepository(session)
        assert [cat.code for cat in repo.list_categories()] == ["pwn", "re", "web"]

        request = repo.create_generation_request(
            category="web",
            topic="SQL injection",
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
            seed_urls=("https://example.com/a",),
        )
        session.commit()

        assert repo.get_generation_request(request.id) == request
        assert [req.id for req in repo.list_generation_requests(category="web")] == [request.id]
        assert repo.list_generation_requests(category="pwn") == []
    finally:
        session.close()


def test_dynamic_category_and_distribution_validation(session_factory: SessionFactory):
    session = session_factory()
    try:
        repo = ResearchRepository(session)
        with pytest.raises(ResearchValidationError, match="crypto"):
            repo.create_generation_request(
                category="crypto",
                topic="lattice",
                target_count=1,
                difficulty_distribution={"easy": 1},
            )

        session.add(
            model.ChallengeCategory(
                code="crypto",
                display_name="Crypto",
                description="cryptography",
            )
        )
        session.flush()
        created = repo.create_generation_request(
            category="crypto",
            topic="lattice",
            target_count=1,
            difficulty_distribution={"easy": 1},
        )
        assert created.category == "crypto"

        with pytest.raises(ResearchValidationError, match="sums to"):
            repo.create_generation_request(
                category="web",
                topic="bad distribution",
                target_count=3,
                difficulty_distribution={"easy": 1},
            )
    finally:
        session.rollback()
        session.close()


def test_sources_findings_and_cross_run_validation(session_factory: SessionFactory):
    _, run1 = _create_request_and_run(session_factory)
    _, run2 = _create_request_and_run(session_factory)
    session = session_factory()
    try:
        repo = ResearchRepository(session)
        source1 = repo.add_source(
            run1.id,
            url="https://example.com/1",
            title="one",
            summary="summary",
            content_hash="hash-1",
            fetched_at=datetime.now(timezone.utc),
        )
        source2 = repo.add_source(
            run2.id,
            url="https://example.com/2",
            title="two",
            summary="summary",
            content_hash="hash-2",
            fetched_at=datetime.now(timezone.utc),
        )
        finding = repo.create_finding(
            run1.id,
            kind="technique",
            label="union",
            summary="UNION SELECT",
            source_ids=[source1.id],
        )
        assert repo.list_sources(run1.id) == [source1]
        assert repo.list_findings(run1.id) == [finding]

        with pytest.raises(ResearchValidationError, match="at least one source"):
            repo.create_finding(
                run1.id,
                kind="technique",
                label="empty",
                summary="bad",
                source_ids=[],
            )
        with pytest.raises(ResearchValidationError, match="do not belong"):
            repo.create_finding(
                run1.id,
                kind="technique",
                label="cross-run",
                summary="bad",
                source_ids=[source2.id],
            )
        with pytest.raises(ResearchValidationError, match="do not exist"):
            repo.create_finding(
                run1.id,
                kind="technique",
                label="missing",
                summary="bad",
                source_ids=[run1.id],
            )
    finally:
        session.rollback()
        session.close()


def test_profile_binding_methods(session_factory: SessionFactory):
    session = session_factory()
    try:
        repo = ResearchRepository(session)
        default = repo.get_binding("research")
        assert default is not None
        assert (default.role, default.profile_name, default.status) == ("research", "default", "enabled")

        updated = repo.upsert_binding("research", "ctf-bot", description="bot")
        assert updated.profile_name == "ctf-bot"
        assert updated.description == "bot"

        disabled = repo.set_binding_status("research", "disabled")
        assert disabled.status == "disabled"

        with pytest.raises(ResearchValidationError, match="unknown_role"):
            repo.upsert_binding("unknown_role", "x")
        with pytest.raises(ResearchValidationError, match="invalid"):
            repo.set_binding_status("research", "invalid")

        _, run = _create_request_and_run(session_factory)
        before = repo.get_binding("research")
        assert before is not None
        repo.touch_binding("research", last_used_at=datetime.now(timezone.utc), last_used_run_id=run.id)
        after = repo.get_binding("research")
        assert after is not None
        assert after.status == before.status
        assert after.profile_name == before.profile_name
        assert after.last_used_run_id == run.id
    finally:
        session.rollback()
        session.close()


def test_claim_heartbeat_and_failure_retry(session_factory: SessionFactory):
    service = ResearchJobService(session_factory)
    # Only seed 2 requests so that after both workers claim, the queue is empty.
    # The retry row created by mark_run_failed is then the only queued candidate,
    # avoiding a race against an older queued seed row under the FIFO ordering of
    # claim_next_run.
    for topic in ("one", "two"):
        service.submit_request("web", topic, 1, {"easy": 1})

    def claim(worker: str):
        return service.claim_next_run(worker, 60)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, ["w1", "w2"]))
    assert claimed[0] is not None
    assert claimed[1] is not None
    assert claimed[0].id != claimed[1].id
    assert claimed[0].claim_token != claimed[1].claim_token

    first = claimed[0]
    assert first is not None
    assert first.claim_token is not None
    assert service.heartbeat(first.id, "wrong", first.claim_token, 60) is False
    assert service.heartbeat(first.id, first.claimed_by or "", first.claim_token, 60) is True

    failed = service.mark_run_failed(first.id, first.claimed_by or "", first.claim_token, "bad json")
    assert failed.status == "failed"
    assert failed.was_retried is True
    runs = service.claim_next_run("retry-worker", 60)
    assert runs is not None
    assert runs.parent_run_id == first.id
    assert runs.attempt == 2

    with session_factory() as session:
        stats = ResearchRepository(session).queue_stats()
        assert stats["queued"] == 0  # only run is now running, claimed by retry-worker
        assert stats["running"] >= 2
        assert stats["failed"] >= 1
        assert isinstance(stats["runs_near_lease_expiry"], list)


def test_expired_lease_recovery_and_max_attempt_failure(session_factory: SessionFactory):
    service = ResearchJobService(session_factory)
    _, run = service.submit_request("web", "lease", 1, {"easy": 1}, max_attempts=1)
    claimed = service.claim_next_run("w1", 60)
    assert claimed is not None

    with session_factory() as session:
        row = session.get(model.ResearchRun, claimed.id)
        assert row is not None
        old_token = row.claim_token
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()

    assert service.claim_next_run("w2", 60) is None

    with session_factory() as session:
        expired = session.get(model.ResearchRun, run.id)
        assert expired is not None
        assert expired.status == "failed"
        assert expired.claim_token == old_token
        request = session.get(model.GenerationRequest, run.generation_request_id)
        assert request is not None
        assert request.status == "failed"


def test_token_fencing_and_complete_run_with_results_atomicity(session_factory: SessionFactory):
    service = ResearchJobService(session_factory)
    _, run = service.submit_request("web", "complete", 1, {"easy": 1})
    claimed = service.claim_next_run("w1", 60)
    assert claimed is not None
    assert claimed.claim_token is not None

    with pytest.raises(StaleClaimError):
        service.mark_run_completed(claimed.id, "w1", run.id, log_path="log")

    with pytest.raises(ResearchValidationError, match="out of range"):
        service.complete_run_with_results(
            claimed.id,
            "w1",
            claimed.claim_token,
            sources=[
                {
                    "url": "https://example.com",
                    "title": "Example",
                    "summary": "Summary",
                    "content_hash": "hash",
                }
            ],
            findings=[
                {
                    "kind": "technique",
                    "label": "bad",
                    "summary": "bad",
                    "source_indices": [1],
                }
            ],
            binding_role="research",
            log_path="log",
        )
    with session_factory() as session:
        repo = ResearchRepository(session)
        assert repo.list_sources(claimed.id) == []

    completed = service.complete_run_with_results(
        claimed.id,
        "w1",
        claimed.claim_token,
        sources=[
            {
                "url": "https://example.com",
                "title": "Example",
                "summary": "Summary",
                "content_hash": "hash",
            }
        ],
        findings=[
            {
                "kind": "technique",
                "label": "union",
                "summary": "UNION SELECT",
                "source_indices": [0],
            }
        ],
        binding_role="research",
        log_path="log",
    )
    assert completed.status == "completed"

    with session_factory() as session:
        binding = ResearchRepository(session).get_binding("research")
        assert binding is not None
        assert binding.last_used_run_id == claimed.id


def test_attempt_greater_than_max_attempts_is_rejected(session_factory: SessionFactory):
    service = ResearchJobService(session_factory)
    _, run = service.submit_request("web", "tamper", 1, {"easy": 1}, max_attempts=1)
    claimed = service.claim_next_run("w1", 60)
    assert claimed is not None
    assert claimed.claim_token is not None
    with session_factory() as session:
        row = session.get(model.ResearchRun, run.id)
        assert row is not None
        row.attempt = 2
        session.commit()

    with pytest.raises(ResearchAttemptError):
        service.mark_run_failed(claimed.id, "w1", claimed.claim_token, "tampered")
