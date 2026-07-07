"""PostgreSQL-backed tests for corpus-governance repositories."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from domain.challenge_corpus import (
    CORPUS_FINGERPRINT_SCHEMA_VERSION,
    CorpusBatchStatus,
    CorpusDecisionScope,
    CorpusDecisionValue,
    CorpusMode,
    CorpusReviewDecisionValue,
    ObservationReviewDecisionValue,
    corpus_decision_is_effectively_accepted,
    corpus_review_allows_acceptance,
    observation_review_allows_acceptance,
)
from persistence.models import (
    ArtifactObservation,
    BuildAttempt,
    ChallengeCategory,
    ChallengeDesign,
    DesignAttempt,
    DesignEvidence,
    DesignTask,
    GenerationRequest,
    ResearchRun,
)
from persistence.models import challenge_corpus as corpus_model
from persistence.repositories import CorpusPersistenceError, CorpusRepository
from persistence.session import SessionFactory

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def session_factory() -> SessionFactory:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
    try:
        yield SessionFactory(engine)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory):
    with session_factory() as session:
        for table in (
            corpus_model.CorpusMatch,
            corpus_model.CorpusReviewDecision,
            corpus_model.ObservationReviewDecision,
            corpus_model.CorpusDecision,
            corpus_model.CorpusBatchMember,
            corpus_model.CorpusHistoryEntry,
            corpus_model.CorpusBatch,
            ArtifactObservation,
            BuildAttempt,
            DesignEvidence,
            ChallengeDesign,
            DesignAttempt,
            DesignTask,
            ResearchRun,
            GenerationRequest,
        ):
            session.execute(sa.delete(table))
        session.execute(
            sa.delete(ChallengeCategory).where(ChallengeCategory.code.not_in(["web", "pwn", "re"]))
        )
        session.commit()
    yield


def _seed_candidate(session_factory: SessionFactory):
    with session_factory() as session:
        session.merge(ChallengeCategory(code="web", display_name="Web", description=None))
        request_id = uuid4()
        run_id = uuid4()
        task_id = uuid4()
        attempt_id = uuid4()
        design_attempt_id = uuid4()
        design_id = uuid4()
        evidence_id = uuid4()
        observation_id = uuid4()
        session.add(
            GenerationRequest(
                id=request_id,
                category="web",
                topic="SQL injection",
                target_count=1,
                difficulty_distribution={"easy": 1},
                status="researched",
            )
        )
        session.add(
            ResearchRun(
                id=run_id,
                generation_request_id=request_id,
                attempt=1,
                status="completed",
            )
        )
        session.add(
            DesignTask(
                id=task_id,
                generation_request_id=request_id,
                research_run_id=run_id,
                task_no=1,
                challenge_id="web-0001",
                title="Demo",
                category="web",
                difficulty="easy",
                primary_technique="sqli",
                learning_objective="Practice SQLi",
                points=100,
                status="built",
            )
        )
        session.add(
            DesignAttempt(
                id=design_attempt_id,
                design_task_id=task_id,
                attempt=1,
                status="completed",
                claim_token=uuid4(),
                profile_name_used="default",
            )
        )
        session.add(
            ChallengeDesign(
                id=design_id,
                design_task_id=task_id,
                design_attempt_id=design_attempt_id,
                payload={},
                summary="Demo",
                flag_format="flag{}",
                validation_notes="ok",
                quality_gate_passed=True,
                status="accepted",
            )
        )
        session.add(
            DesignEvidence(
                id=evidence_id,
                design_task_id=task_id,
                evidence_version=1,
                challenge_design_id=design_id,
                research_finding_ids=[],
                profile={},
                profile_signature="profile",
                distinctness_claim="distinct solve and implementation",
                compared_challenge_ids=[],
                evidence={},
                build_contract={},
                ledger_version=1,
            )
        )
        session.add(
            BuildAttempt(
                id=attempt_id,
                design_task_id=task_id,
                attempt_no=1,
                status="succeeded",
                shard_basename="web-0001.json",
                artifact_status="present",
                design_evidence_id=evidence_id,
                contract_sha256="contract",
            )
        )
        session.add(
            ArtifactObservation(
                id=observation_id,
                build_attempt_id=attempt_id,
                observation_version=1,
                design_evidence_id=evidence_id,
                contract_sha256="contract",
                artifact_manifest_sha256="artifact",
                observed_profile={},
                contract_checks={},
                negative_test_results={},
                fingerprints={"combined": "abc"},
                status="passed",
                is_current=True,
            )
        )
        session.commit()
        return attempt_id, evidence_id, observation_id


def _seed_candidate_with_id(session_factory: SessionFactory, challenge_id: str):
    with session_factory() as session:
        session.merge(ChallengeCategory(code="web", display_name="Web", description=None))
        request_id = uuid4()
        run_id = uuid4()
        task_id = uuid4()
        attempt_id = uuid4()
        design_attempt_id = uuid4()
        design_id = uuid4()
        evidence_id = uuid4()
        observation_id = uuid4()
        session.add(
            GenerationRequest(
                id=request_id,
                category="web",
                topic="SQL injection",
                target_count=1,
                difficulty_distribution={"easy": 1},
                status="researched",
            )
        )
        session.add(
            ResearchRun(
                id=run_id,
                generation_request_id=request_id,
                attempt=1,
                status="completed",
            )
        )
        session.add(
            DesignTask(
                id=task_id,
                generation_request_id=request_id,
                research_run_id=run_id,
                task_no=1,
                challenge_id=challenge_id,
                title="Demo",
                category="web",
                difficulty="easy",
                primary_technique="sqli",
                learning_objective="Practice SQLi",
                points=100,
                status="built",
            )
        )
        session.add(
            DesignAttempt(
                id=design_attempt_id,
                design_task_id=task_id,
                attempt=1,
                status="completed",
                claim_token=uuid4(),
                profile_name_used="default",
            )
        )
        session.add(
            ChallengeDesign(
                id=design_id,
                design_task_id=task_id,
                design_attempt_id=design_attempt_id,
                payload={},
                summary="Demo",
                flag_format="flag{}",
                validation_notes="ok",
                quality_gate_passed=True,
                status="accepted",
            )
        )
        session.add(
            DesignEvidence(
                id=evidence_id,
                design_task_id=task_id,
                evidence_version=1,
                challenge_design_id=design_id,
                research_finding_ids=[],
                profile={},
                profile_signature="profile",
                distinctness_claim="distinct solve and implementation",
                compared_challenge_ids=[],
                evidence={},
                build_contract={},
                ledger_version=1,
            )
        )
        session.add(
            BuildAttempt(
                id=attempt_id,
                design_task_id=task_id,
                attempt_no=1,
                status="succeeded",
                shard_basename=f"{challenge_id}.json",
                artifact_status="present",
                design_evidence_id=evidence_id,
                contract_sha256="contract",
            )
        )
        session.add(
            ArtifactObservation(
                id=observation_id,
                build_attempt_id=attempt_id,
                observation_version=1,
                design_evidence_id=evidence_id,
                contract_sha256="contract",
                artifact_manifest_sha256="artifact",
                observed_profile={},
                contract_checks={},
                negative_test_results={},
                fingerprints={"combined": challenge_id},
                status="passed",
                is_current=True,
            )
        )
        session.commit()
        return attempt_id, evidence_id, observation_id


def _fingerprints() -> dict[str, object]:
    return {
        "schema_version": CORPUS_FINGERPRINT_SCHEMA_VERSION,
        "combined": "combined",
        "source": {"sha256": "source", "tokens": ["select"], "token_count": 1},
    }


def test_batch_membership_becomes_immutable_after_evaluation_starts(
    session_factory: SessionFactory,
) -> None:
    attempt_id, evidence_id, observation_id = _seed_candidate(session_factory)
    with session_factory() as session:
        repo = CorpusRepository(session)
        batch = repo.create_batch(
            mode=CorpusMode.PRODUCTION.value,
            category="web",
            policy_version=1,
            created_by="operator",
        )
        member = repo.add_member(
            batch_id=batch.id,
            build_attempt_id=attempt_id,
            design_evidence_id=evidence_id,
            artifact_observation_id=observation_id,
            fingerprint_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
            fingerprints=_fingerprints(),
        )
        assert member.batch_id == batch.id

        started = repo.start_evaluation(batch.id)
        assert started.status == CorpusBatchStatus.EVALUATING.value
        with pytest.raises(CorpusPersistenceError, match="immutable"):
            repo.add_member(
                batch_id=batch.id,
                build_attempt_id=uuid4(),
                design_evidence_id=evidence_id,
                artifact_observation_id=observation_id,
                fingerprint_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
                fingerprints=_fingerprints(),
            )


def test_record_decision_supersedes_previous_current_decision(
    session_factory: SessionFactory,
) -> None:
    attempt_id, evidence_id, observation_id = _seed_candidate(session_factory)
    with session_factory() as session:
        repo = CorpusRepository(session)
        batch = repo.create_batch(
            mode=CorpusMode.TRIAL.value,
            category="web",
            policy_version=1,
            created_by="operator",
        )
        member = repo.add_member(
            batch_id=batch.id,
            build_attempt_id=attempt_id,
            design_evidence_id=evidence_id,
            artifact_observation_id=observation_id,
            fingerprint_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
            fingerprints=_fingerprints(),
        )
        first = repo.record_decision(
            batch_id=batch.id,
            member_id=member.id,
            scope=CorpusDecisionScope.MEMBER.value,
            decision=CorpusDecisionValue.REVIEW_REQUIRED.value,
            reasons=["source_similarity"],
            policy_version=1,
        )
        second = repo.record_decision(
            batch_id=batch.id,
            member_id=member.id,
            scope=CorpusDecisionScope.MEMBER.value,
            decision=CorpusDecisionValue.PASSED.value,
            reasons=["reviewed"],
            policy_version=1,
        )
        rows = session.scalars(sa.select(corpus_model.CorpusDecision)).all()
        assert {row.id for row in rows} == {first.id, second.id}
        assert sum(1 for row in rows if row.is_current) == 1
        assert session.get(corpus_model.CorpusDecision, first.id).superseded_at is not None
        assert session.get(corpus_model.CorpusDecision, second.id).is_current is True


def test_reviews_do_not_rewrite_current_decision(session_factory: SessionFactory) -> None:
    attempt_id, evidence_id, observation_id = _seed_candidate(session_factory)
    with session_factory() as session:
        repo = CorpusRepository(session)
        batch = repo.create_batch(
            mode=CorpusMode.PRODUCTION.value,
            category="web",
            policy_version=1,
            created_by="operator",
        )
        member = repo.add_member(
            batch_id=batch.id,
            build_attempt_id=attempt_id,
            design_evidence_id=evidence_id,
            artifact_observation_id=observation_id,
            fingerprint_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
            fingerprints=_fingerprints(),
        )
        decision = repo.record_decision(
            batch_id=batch.id,
            member_id=member.id,
            scope=CorpusDecisionScope.MEMBER.value,
            decision=CorpusDecisionValue.REVIEW_REQUIRED.value,
            reasons=["source_similarity"],
            policy_version=1,
        )

        corpus_review = repo.record_corpus_review(
            corpus_decision_id=decision.id,
            decision=CorpusReviewDecisionValue.APPROVED.value,
            actor="operator",
            reason="acceptable overlap",
            scope="production-publication",
        )
        observation_review = repo.record_observation_review(
            artifact_observation_id=observation_id,
            decision=ObservationReviewDecisionValue.ACCEPTED.value,
            actor="operator",
            reason="observer lacks source provenance",
            scope="validation-success",
        )

        current = repo.current_decision(
            batch_id=batch.id,
            member_id=member.id,
            scope=CorpusDecisionScope.MEMBER.value,
        )
        assert current is not None
        assert current.id == decision.id
        assert current.decision == CorpusDecisionValue.REVIEW_REQUIRED.value
        assert repo.latest_corpus_review(corpus_decision_id=decision.id) == corpus_review
        assert repo.latest_observation_review(
            artifact_observation_id=observation_id
        ) == observation_review
        assert corpus_review_allows_acceptance(corpus_review)
        assert corpus_decision_is_effectively_accepted(
            current,
            has_allowed_review=corpus_review_allows_acceptance(corpus_review),
        )
        assert observation_review_allows_acceptance(observation_review)


def test_production_eligible_challenge_ids_require_accepted_corpus_rows(
    session_factory: SessionFactory,
) -> None:
    accepted_attempt, accepted_evidence, accepted_observation = _seed_candidate_with_id(
        session_factory,
        "web-accepted",
    )
    blocked_attempt, blocked_evidence, blocked_observation = _seed_candidate_with_id(
        session_factory,
        "web-blocked",
    )
    with session_factory() as session:
        repo = CorpusRepository(session)
        batch = repo.create_batch(
            mode=CorpusMode.PRODUCTION.value,
            category="web",
            policy_version=1,
            created_by="operator",
        )
        accepted_member = repo.add_member(
            batch_id=batch.id,
            build_attempt_id=accepted_attempt,
            design_evidence_id=accepted_evidence,
            artifact_observation_id=accepted_observation,
            fingerprint_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
            fingerprints=_fingerprints(),
        )
        blocked_member = repo.add_member(
            batch_id=batch.id,
            build_attempt_id=blocked_attempt,
            design_evidence_id=blocked_evidence,
            artifact_observation_id=blocked_observation,
            fingerprint_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
            fingerprints=_fingerprints(),
        )
        repo.record_decision(
            batch_id=batch.id,
            member_id=accepted_member.id,
            scope=CorpusDecisionScope.MEMBER.value,
            decision=CorpusDecisionValue.PASSED.value,
            reasons=["ok"],
            policy_version=1,
        )
        repo.record_decision(
            batch_id=batch.id,
            member_id=blocked_member.id,
            scope=CorpusDecisionScope.MEMBER.value,
            decision=CorpusDecisionValue.BLOCKED.value,
            reasons=["exact_combined_duplicate"],
            policy_version=1,
        )
        repo.record_decision(
            batch_id=batch.id,
            scope=CorpusDecisionScope.AGGREGATE.value,
            decision=CorpusDecisionValue.PASSED.value,
            reasons=["all_members_accepted"],
            policy_version=1,
        )

        assert repo.production_eligible_challenge_ids(batch_id=batch.id) == set()


def test_production_eligible_challenge_ids_allow_reviewed_member_with_passed_aggregate(
    session_factory: SessionFactory,
) -> None:
    attempt_id, evidence_id, observation_id = _seed_candidate_with_id(
        session_factory,
        "web-reviewed",
    )
    with session_factory() as session:
        repo = CorpusRepository(session)
        batch = repo.create_batch(
            mode=CorpusMode.PRODUCTION.value,
            category="web",
            policy_version=1,
            created_by="operator",
        )
        member = repo.add_member(
            batch_id=batch.id,
            build_attempt_id=attempt_id,
            design_evidence_id=evidence_id,
            artifact_observation_id=observation_id,
            fingerprint_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
            fingerprints=_fingerprints(),
        )
        decision = repo.record_decision(
            batch_id=batch.id,
            member_id=member.id,
            scope=CorpusDecisionScope.MEMBER.value,
            decision=CorpusDecisionValue.REVIEW_REQUIRED.value,
            reasons=["source_similarity_review"],
            policy_version=1,
        )
        repo.record_corpus_review(
            corpus_decision_id=decision.id,
            decision=CorpusReviewDecisionValue.APPROVED.value,
            actor="operator",
            reason="acceptable overlap",
            scope="production-publication",
        )
        repo.record_decision(
            batch_id=batch.id,
            scope=CorpusDecisionScope.AGGREGATE.value,
            decision=CorpusDecisionValue.PASSED.value,
            reasons=["all_members_accepted"],
            policy_version=1,
        )

        assert repo.production_eligible_challenge_ids(batch_id=batch.id) == {
            "web-reviewed"
        }
        current = repo.current_decision(
            batch_id=batch.id,
            member_id=member.id,
            scope=CorpusDecisionScope.MEMBER.value,
        )
        assert current is not None
        assert current.decision == CorpusDecisionValue.REVIEW_REQUIRED.value


def test_production_eligible_challenge_ids_require_aggregate_pass(
    session_factory: SessionFactory,
) -> None:
    attempt_id, evidence_id, observation_id = _seed_candidate_with_id(
        session_factory,
        "web-reviewed",
    )
    with session_factory() as session:
        repo = CorpusRepository(session)
        batch = repo.create_batch(
            mode=CorpusMode.PRODUCTION.value,
            category="web",
            policy_version=1,
            created_by="operator",
        )
        member = repo.add_member(
            batch_id=batch.id,
            build_attempt_id=attempt_id,
            design_evidence_id=evidence_id,
            artifact_observation_id=observation_id,
            fingerprint_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
            fingerprints=_fingerprints(),
        )
        decision = repo.record_decision(
            batch_id=batch.id,
            member_id=member.id,
            scope=CorpusDecisionScope.MEMBER.value,
            decision=CorpusDecisionValue.REVIEW_REQUIRED.value,
            reasons=["source_similarity_review"],
            policy_version=1,
        )
        repo.record_corpus_review(
            corpus_decision_id=decision.id,
            decision=CorpusReviewDecisionValue.APPROVED.value,
            actor="operator",
            reason="acceptable overlap",
            scope="production-publication",
        )
        repo.record_decision(
            batch_id=batch.id,
            scope=CorpusDecisionScope.AGGREGATE.value,
            decision=CorpusDecisionValue.REVIEW_REQUIRED.value,
            reasons=["member_review_required:source_similarity_review"],
            policy_version=1,
        )

        assert repo.production_eligible_challenge_ids(batch_id=batch.id) == set()
