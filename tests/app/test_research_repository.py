"""Postgres-backed tests for research repository and queue primitives."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

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
        # Leave the test DB clean so the next test module's fixtures see
        # an empty schema. Without this, pytest-randomly or running this
        # file before test_alembic_migrations.py would leave 8 stale
        # application tables behind, breaking the baseline-only assertion
        # in that test. check=False so a partial failure during the test
        # module does not mask the original error.
        subprocess.run(
            ["uv", "run", "alembic", "downgrade", "base"],
            cwd=ROOT,
            env=env,
            check=False,
        )


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


def test_latest_run_lookup_is_not_limited_to_first_page(session_factory: SessionFactory):
    session = session_factory()
    try:
        repo = ResearchRepository(session)
        request = repo.create_generation_request(
            category="web",
            topic="many attempts",
            target_count=1,
            difficulty_distribution={"easy": 1},
        )
        base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for attempt in range(1, 102):
            row = model.ResearchRun(
                id=uuid4(),
                generation_request_id=request.id,
                attempt=attempt,
                status="failed" if attempt < 101 else "completed",
                created_at=base_time + timedelta(days=attempt),
            )
            session.add(row)
        session.flush()

        listed = repo.list_runs(generation_request_id=request.id)
        latest = repo.get_latest_run_for_request(request.id)

        assert len(listed) == 100
        assert latest is not None
        assert latest.attempt == 101
        assert listed[-1].attempt == 100
    finally:
        session.rollback()
        session.close()


def test_latest_completed_run_ignores_newer_running_retry(session_factory: SessionFactory):
    session = session_factory()
    try:
        repo = ResearchRepository(session)
        request = repo.create_generation_request(
            category="web",
            topic="retry after success",
            target_count=1,
            difficulty_distribution={"easy": 1},
        )
        base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        completed = model.ResearchRun(
            id=uuid4(),
            generation_request_id=request.id,
            attempt=1,
            status="completed",
            created_at=base_time,
            finished_at=base_time + timedelta(minutes=5),
        )
        running = model.ResearchRun(
            id=uuid4(),
            generation_request_id=request.id,
            attempt=2,
            status="running",
            created_at=base_time + timedelta(minutes=10),
        )
        session.add_all([completed, running])
        session.flush()

        latest = repo.get_latest_run_for_request(request.id)
        latest_completed = repo.get_latest_completed_run_for_request(request.id)

        assert latest is not None
        assert latest.id == running.id
        assert latest_completed is not None
        assert latest_completed.id == completed.id
    finally:
        session.rollback()
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
            technique_family="injection",
        )
        assert repo.list_sources(run1.id) == [source1]
        assert repo.list_findings(run1.id) == [finding]
        assert finding.technique_family == "injection"

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


def test_queue_stats_excludes_exact_lease_expiry_boundary(session_factory: SessionFactory):
    session = session_factory()
    try:
        request = model.GenerationRequest(
            id=uuid4(),
            category="web",
            topic="boundary",
            target_count=1,
            difficulty_distribution={"easy": 1},
            runtime_constraints={},
            seed_urls=[],
            max_attempts=1,
            status="researching",
        )
        exact_boundary = model.ResearchRun(
            id=uuid4(),
            generation_request_id=request.id,
            attempt=1,
            status="running",
            lease_expires_at=sa.func.now() + sa.text("interval '60 seconds'"),
        )
        inside_boundary = model.ResearchRun(
            id=uuid4(),
            generation_request_id=request.id,
            attempt=2,
            status="running",
            lease_expires_at=sa.func.now() + sa.text("interval '59 seconds'"),
        )
        session.add_all([request, exact_boundary, inside_boundary])
        session.flush()

        stats = ResearchRepository(session).queue_stats()

        assert exact_boundary.id not in stats["runs_near_lease_expiry"]
        assert inside_boundary.id in stats["runs_near_lease_expiry"]
    finally:
        session.rollback()
        session.close()


def test_idempotency_key_serializes_hits_conflicts_and_ttl(
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    service = ResearchJobService(session_factory)

    def submit(topic="same"):
        return service.submit_request(
            "web",
            topic,
            1,
            {"easy": 1},
            idempotency_key="operator-key",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: submit(), range(2)))
    assert results[0][0].id == results[1][0].id
    with session_factory() as session:
        assert session.scalar(
            sa.select(sa.func.count()).where(
                model.GenerationRequest.idempotency_key == "operator-key"
            )
        ) == 1

    with pytest.raises(ResearchValidationError, match="idempotency_key_conflict"):
        submit("different")

    monkeypatch.setenv("RESEARCH_SUBMIT_IDEMPOTENCY_TTL_SECONDS", "1")
    with session_factory() as session:
        row = session.scalar(
            sa.select(model.GenerationRequest).where(
                model.GenerationRequest.idempotency_key == "operator-key"
            )
        )
        row.created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        session.commit()
    newer, _run = submit()
    assert newer.id != results[0][0].id


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


def test_supplement_run_preserves_parent_findings_when_output_is_empty(
    session_factory: SessionFactory,
):
    service = ResearchJobService(session_factory)
    request, _run = service.submit_request(
        "web",
        "underfilled supplement",
        12,
        {"easy": 12},
        max_attempts=3,
    )
    first = service.claim_next_run("w1", 60, generation_request_id=request.id)
    assert first is not None
    assert first.claim_token is not None

    service.complete_run_with_results(
        first.id,
        "w1",
        first.claim_token,
        sources=[
            {
                "url": f"https://example.com/{index}",
                "title": f"Source {index}",
                "summary": f"Summary {index}",
                "content_hash": f"hash-{index}",
            }
            for index in range(5)
        ],
        findings=[
            {
                "kind": "technique",
                "label": f"Finding {index}",
                "summary": f"Finding summary {index}",
                "source_indices": [index],
            }
            for index in range(5)
        ],
        binding_role="research",
        log_path="first.log",
    )

    supplement = service.ensure_supplement_run(request.id)
    claimed = service.claim_next_run("w2", 60, generation_request_id=request.id)
    assert claimed is not None
    assert claimed.id == supplement.id
    assert claimed.claim_token is not None
    service.complete_run_with_results(
        claimed.id,
        "w2",
        claimed.claim_token,
        sources=[],
        findings=[],
        binding_role="research",
        log_path="supplement.log",
    )

    with session_factory() as session:
        repo = ResearchRepository(session)
        latest = repo.get_latest_completed_run_for_request(request.id)
        assert latest is not None
        assert latest.id == supplement.id
        assert len(repo.list_sources(latest.id)) == 5
        assert len(repo.list_findings(latest.id)) == 5


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


def test_complete_run_with_results_rejects_malformed_payloads(session_factory: SessionFactory):
    """Defensive validation in complete_run_with_results: empty strings,
    None values where strings are required, malformed source_indices
    types, and mutually exclusive source_ids+source_indices are all
    rejected before any DB write.
    """
    service = ResearchJobService(session_factory)
    _, run = service.submit_request("web", "edge-cases", 1, {"easy": 1})
    claimed = service.claim_next_run("w1", 60)
    assert claimed is not None

    base_kwargs = dict(
        run_id=claimed.id,
        agent_id="w1",
        claim_token=claimed.claim_token,
        binding_role="research",
        log_path="/tmp/x.log",
    )

    # (1) source url is None -> rejected (no "None" string stored)
    with pytest.raises(ResearchValidationError):
        service.complete_run_with_results(
            sources=[{"url": None, "title": "t", "summary": "s", "content_hash": "h"}],
            findings=[],
            **base_kwargs,
        )

    # (2) source missing required field
    with pytest.raises(ResearchValidationError):
        service.complete_run_with_results(
            sources=[{"url": "https://x", "title": "t", "summary": "s"}],  # no content_hash
            findings=[],
            **base_kwargs,
        )

    # (3) source field empty string
    with pytest.raises(ResearchValidationError):
        service.complete_run_with_results(
            sources=[{"url": "", "title": "t", "summary": "s", "content_hash": "h"}],
            findings=[],
            **base_kwargs,
        )

    # (4) finding source_indices is a string, NOT a list (str is a Sequence!)
    with pytest.raises(ResearchValidationError):
        service.complete_run_with_results(
            sources=[
                {"url": "https://x", "title": "t", "summary": "s", "content_hash": "h"}
            ],
            findings=[
                {
                    "kind": "technique",
                    "label": "l",
                    "summary": "s",
                    "source_indices": "0",
                }
            ],
            **base_kwargs,
        )

    # (5) finding has both source_ids and source_indices (ambiguous)
    with pytest.raises(ResearchValidationError):
        service.complete_run_with_results(
            sources=[
                {"url": "https://x", "title": "t", "summary": "s", "content_hash": "h"}
            ],
            findings=[
                {
                    "kind": "technique",
                    "label": "l",
                    "summary": "s",
                    "source_ids": [],
                    "source_indices": [0],
                }
            ],
            **base_kwargs,
        )

    # All five rejections rolled back; the claim is still ours, still running.
    with session_factory() as session:
        run_row = ResearchRepository(session).get_run(claimed.id)
        assert run_row is not None
        assert run_row.status == "running"
        assert run_row.claim_token == claimed.claim_token
        assert ResearchRepository(session).list_sources(claimed.id) == []
        assert ResearchRepository(session).list_findings(claimed.id) == []
