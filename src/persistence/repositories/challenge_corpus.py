"""Persistence primitives for corpus-governance rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.clock import utcnow as _utcnow
from domain import challenge_corpus as dto
from persistence.models import artifact_observations as observation_model
from persistence.models import build_attempts as build_model
from persistence.models import challenge_corpus as model
from persistence.models import challenge_designs as design_model
from persistence.models import design_tasks as task_model
from persistence.models import research as research_model


class CorpusPersistenceError(ValueError):
    """Raised when corpus-governance persistence semantics are violated."""


class CorpusRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_batch(
        self,
        *,
        mode: str,
        category: str,
        policy_version: int,
        created_by: str,
        batch_id: UUID | None = None,
    ) -> dto.CorpusBatch:
        if mode not in {item.value for item in dto.CorpusMode}:
            raise CorpusPersistenceError(f"unknown corpus mode {mode!r}")
        if policy_version <= 0:
            raise CorpusPersistenceError("policy_version must be positive")
        if not created_by.strip():
            raise CorpusPersistenceError("created_by is required")
        row = model.CorpusBatch(
            id=batch_id or uuid4(),
            mode=mode,
            category=category,
            policy_version=policy_version,
            status=dto.CorpusBatchStatus.DRAFT.value,
            created_by=created_by.strip(),
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _batch(row)

    def get_batch(self, batch_id: UUID) -> dto.CorpusBatch | None:
        row = self.session.get(model.CorpusBatch, batch_id)
        return _batch(row) if row else None

    def start_evaluation(self, batch_id: UUID) -> dto.CorpusBatch:
        row = self._lock_batch(batch_id)
        if row.status != dto.CorpusBatchStatus.DRAFT.value:
            raise CorpusPersistenceError(
                f"corpus batch {batch_id} is {row.status}, expected draft"
            )
        row.status = dto.CorpusBatchStatus.EVALUATING.value
        row.evaluation_started_at = _utcnow()
        self.session.flush()
        self.session.refresh(row)
        return _batch(row)

    def mark_evaluated(self, batch_id: UUID) -> dto.CorpusBatch:
        row = self._lock_batch(batch_id)
        if row.status != dto.CorpusBatchStatus.EVALUATING.value:
            raise CorpusPersistenceError(
                f"corpus batch {batch_id} is {row.status}, expected evaluating"
            )
        row.status = dto.CorpusBatchStatus.EVALUATED.value
        row.evaluated_at = _utcnow()
        self.session.flush()
        self.session.refresh(row)
        return _batch(row)

    def add_member(
        self,
        *,
        batch_id: UUID,
        build_attempt_id: UUID,
        design_evidence_id: UUID,
        artifact_observation_id: UUID,
        fingerprint_version: int,
        fingerprints: Mapping[str, Any],
        member_id: UUID | None = None,
    ) -> dto.CorpusBatchMember:
        batch = self._lock_batch(batch_id)
        if batch.status != dto.CorpusBatchStatus.DRAFT.value:
            raise CorpusPersistenceError("corpus batch membership is immutable after evaluation starts")
        if fingerprint_version <= 0:
            raise CorpusPersistenceError("fingerprint_version must be positive")
        row = model.CorpusBatchMember(
            id=member_id or uuid4(),
            batch_id=batch_id,
            build_attempt_id=build_attempt_id,
            design_evidence_id=design_evidence_id,
            artifact_observation_id=artifact_observation_id,
            fingerprint_version=fingerprint_version,
            fingerprints=dict(fingerprints),
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _member(row)

    def list_members(self, batch_id: UUID) -> list[dto.CorpusBatchMember]:
        rows = self.session.scalars(
            sa.select(model.CorpusBatchMember)
            .where(model.CorpusBatchMember.batch_id == batch_id)
            .order_by(model.CorpusBatchMember.created_at, model.CorpusBatchMember.id)
        ).all()
        return [_member(row) for row in rows]

    def list_batch_comparison_targets(
        self,
        *,
        batch_id: UUID,
        exclude_member_id: UUID | None = None,
    ) -> list[dto.CorpusComparisonTarget]:
        stmt = (
            sa.select(
                model.CorpusBatchMember,
                design_model.DesignEvidence.design_task_id,
                task_model.DesignTask.challenge_id,
            )
            .join(
                design_model.DesignEvidence,
                design_model.DesignEvidence.id
                == model.CorpusBatchMember.design_evidence_id,
            )
            .join(
                task_model.DesignTask,
                task_model.DesignTask.id == design_model.DesignEvidence.design_task_id,
            )
            .where(model.CorpusBatchMember.batch_id == batch_id)
            .order_by(model.CorpusBatchMember.created_at, model.CorpusBatchMember.id)
        )
        if exclude_member_id is not None:
            stmt = stmt.where(model.CorpusBatchMember.id != exclude_member_id)
        rows = self.session.execute(stmt).all()
        return [
            dto.CorpusComparisonTarget(
                member_id=member.id,
                design_task_id=design_task_id,
                challenge_id=challenge_id,
                fingerprints=dict(member.fingerprints),
            )
            for member, design_task_id, challenge_id in rows
        ]

    def list_history_shortlist(
        self,
        *,
        category: str,
        fingerprints: Mapping[str, Any],
        limit: int = 100,
    ) -> list[dto.CorpusComparisonTarget]:
        if limit <= 0:
            return []
        stmt = (
            sa.select(
                model.CorpusHistoryEntry,
                design_model.DesignEvidence.design_task_id,
            )
            .outerjoin(
                design_model.DesignEvidence,
                design_model.DesignEvidence.id
                == model.CorpusHistoryEntry.design_evidence_id,
            )
            .where(
                model.CorpusHistoryEntry.category == category,
                model.CorpusHistoryEntry.status.in_(
                    [
                        dto.CorpusHistoryStatus.PUBLISHED.value,
                        dto.CorpusHistoryStatus.RETIRED.value,
                    ]
                ),
            )
            .order_by(model.CorpusHistoryEntry.created_at.desc())
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()
        combined = str(fingerprints.get("combined") or "")
        source_sha = _token_sha(fingerprints, "source")
        solver_sha = _token_sha(fingerprints, "solver")

        def score(row: model.CorpusHistoryEntry) -> tuple[int, str]:
            row_fingerprints = row.fingerprints
            exact = int(combined and row_fingerprints.get("combined") == combined)
            exact += int(source_sha and _token_sha(row_fingerprints, "source") == source_sha)
            exact += int(solver_sha and _token_sha(row_fingerprints, "solver") == solver_sha)
            return exact, str(row.created_at)

        ordered = sorted(rows, key=lambda item: score(item[0]), reverse=True)
        return [
            dto.CorpusComparisonTarget(
                history_entry_id=entry.id,
                design_task_id=design_task_id,
                challenge_id=entry.challenge_id,
                fingerprints=dict(entry.fingerprints),
            )
            for entry, design_task_id in ordered
        ]

    def record_decision(
        self,
        *,
        batch_id: UUID,
        scope: str,
        decision: str,
        reasons: Sequence[str],
        policy_version: int,
        member_id: UUID | None = None,
        decision_id: UUID | None = None,
    ) -> dto.CorpusDecision:
        if scope not in {item.value for item in dto.CorpusDecisionScope}:
            raise CorpusPersistenceError(f"unknown corpus decision scope {scope!r}")
        if decision not in {item.value for item in dto.CorpusDecisionValue}:
            raise CorpusPersistenceError(f"unknown corpus decision {decision!r}")
        if policy_version <= 0:
            raise CorpusPersistenceError("policy_version must be positive")
        if scope == dto.CorpusDecisionScope.MEMBER.value and member_id is None:
            raise CorpusPersistenceError("member decision requires member_id")
        if scope == dto.CorpusDecisionScope.AGGREGATE.value and member_id is not None:
            raise CorpusPersistenceError("aggregate decision cannot have member_id")
        self._supersede_current_decision(batch_id=batch_id, scope=scope, member_id=member_id)
        row = model.CorpusDecision(
            id=decision_id or uuid4(),
            batch_id=batch_id,
            member_id=member_id,
            scope=scope,
            decision=decision,
            reasons=[str(reason) for reason in reasons],
            policy_version=policy_version,
            is_current=True,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _decision(row)

    def record_match(
        self,
        *,
        batch_id: UUID,
        member_id: UUID,
        fingerprint_type: str,
        score: float,
        threshold: float,
        reason: str,
        compared_member_id: UUID | None = None,
        compared_history_entry_id: UUID | None = None,
        match_id: UUID | None = None,
    ) -> dto.CorpusMatch:
        if fingerprint_type not in dto.CORPUS_FINGERPRINT_TYPES:
            raise CorpusPersistenceError(f"unknown fingerprint_type {fingerprint_type!r}")
        if compared_member_id is None and compared_history_entry_id is None:
            raise CorpusPersistenceError("corpus match requires a compared target")
        if not 0 <= score <= 1 or not 0 <= threshold <= 1:
            raise CorpusPersistenceError("score and threshold must be between 0 and 1")
        if not reason.strip():
            raise CorpusPersistenceError("reason is required")
        row = model.CorpusMatch(
            id=match_id or uuid4(),
            batch_id=batch_id,
            member_id=member_id,
            compared_member_id=compared_member_id,
            compared_history_entry_id=compared_history_entry_id,
            fingerprint_type=fingerprint_type,
            score=score,
            threshold=threshold,
            reason=reason.strip(),
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _match(row)

    def record_observation_review(
        self,
        *,
        artifact_observation_id: UUID,
        decision: str,
        actor: str,
        reason: str,
        scope: str,
        review_id: UUID | None = None,
    ) -> dto.ObservationReviewDecision:
        if decision not in {item.value for item in dto.ObservationReviewDecisionValue}:
            raise CorpusPersistenceError(f"unknown observation review decision {decision!r}")
        row = model.ObservationReviewDecision(
            id=review_id or uuid4(),
            artifact_observation_id=artifact_observation_id,
            decision=decision,
            actor=_required_text(actor, "actor"),
            reason=_required_text(reason, "reason"),
            scope=_required_text(scope, "scope"),
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _observation_review(row)

    def latest_observation_review(
        self,
        *,
        artifact_observation_id: UUID,
        scope: str | None = None,
    ) -> dto.ObservationReviewDecision | None:
        stmt = (
            sa.select(model.ObservationReviewDecision)
            .where(
                model.ObservationReviewDecision.artifact_observation_id
                == artifact_observation_id
            )
            .order_by(
                model.ObservationReviewDecision.created_at.desc(),
                model.ObservationReviewDecision.id.desc(),
            )
            .limit(1)
        )
        if scope is not None:
            stmt = stmt.where(model.ObservationReviewDecision.scope == scope)
        row = self.session.scalars(stmt).one_or_none()
        return _observation_review(row) if row else None

    def record_corpus_review(
        self,
        *,
        corpus_decision_id: UUID,
        decision: str,
        actor: str,
        reason: str,
        scope: str,
        review_id: UUID | None = None,
    ) -> dto.CorpusReviewDecision:
        if decision not in {item.value for item in dto.CorpusReviewDecisionValue}:
            raise CorpusPersistenceError(f"unknown corpus review decision {decision!r}")
        row = model.CorpusReviewDecision(
            id=review_id or uuid4(),
            corpus_decision_id=corpus_decision_id,
            decision=decision,
            actor=_required_text(actor, "actor"),
            reason=_required_text(reason, "reason"),
            scope=_required_text(scope, "scope"),
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _corpus_review(row)

    def latest_corpus_review(
        self,
        *,
        corpus_decision_id: UUID,
        scope: str | None = None,
    ) -> dto.CorpusReviewDecision | None:
        stmt = (
            sa.select(model.CorpusReviewDecision)
            .where(model.CorpusReviewDecision.corpus_decision_id == corpus_decision_id)
            .order_by(
                model.CorpusReviewDecision.created_at.desc(),
                model.CorpusReviewDecision.id.desc(),
            )
            .limit(1)
        )
        if scope is not None:
            stmt = stmt.where(model.CorpusReviewDecision.scope == scope)
        row = self.session.scalars(stmt).one_or_none()
        return _corpus_review(row) if row else None

    def current_decision(
        self,
        *,
        batch_id: UUID,
        scope: str,
        member_id: UUID | None = None,
    ) -> dto.CorpusDecision | None:
        if scope not in {item.value for item in dto.CorpusDecisionScope}:
            raise CorpusPersistenceError(f"unknown corpus decision scope {scope!r}")
        stmt = sa.select(model.CorpusDecision).where(
            model.CorpusDecision.batch_id == batch_id,
            model.CorpusDecision.scope == scope,
            model.CorpusDecision.is_current.is_(True),
        )
        if member_id is None:
            stmt = stmt.where(model.CorpusDecision.member_id.is_(None))
        else:
            stmt = stmt.where(model.CorpusDecision.member_id == member_id)
        row = self.session.scalars(stmt.limit(1)).one_or_none()
        return _decision(row) if row else None

    def current_member_decisions(self, batch_id: UUID) -> list[dto.CorpusDecision]:
        rows = self.session.scalars(
            sa.select(model.CorpusDecision)
            .where(
                model.CorpusDecision.batch_id == batch_id,
                model.CorpusDecision.scope == dto.CorpusDecisionScope.MEMBER.value,
                model.CorpusDecision.is_current.is_(True),
            )
            .order_by(model.CorpusDecision.created_at, model.CorpusDecision.id)
        ).all()
        return [_decision(row) for row in rows]

    def member_observation_status(self, member_id: UUID) -> str | None:
        row = self.session.execute(
            sa.select(observation_model.ArtifactObservation.status)
            .join(
                model.CorpusBatchMember,
                model.CorpusBatchMember.artifact_observation_id
                == observation_model.ArtifactObservation.id,
            )
            .where(model.CorpusBatchMember.id == member_id)
            .limit(1)
        ).one_or_none()
        return str(row[0]) if row else None

    def member_research_trial_only(self, member_id: UUID) -> bool:
        row = self.session.execute(
            sa.select(research_model.ResearchRun.trial_only)
            .join(
                task_model.DesignTask,
                task_model.DesignTask.research_run_id == research_model.ResearchRun.id,
            )
            .join(
                design_model.DesignEvidence,
                design_model.DesignEvidence.design_task_id == task_model.DesignTask.id,
            )
            .join(
                model.CorpusBatchMember,
                model.CorpusBatchMember.design_evidence_id == design_model.DesignEvidence.id,
            )
            .where(model.CorpusBatchMember.id == member_id)
            .limit(1)
        ).one_or_none()
        return bool(row[0]) if row else False

    def production_eligible_challenge_ids(
        self,
        *,
        batch_id: UUID,
        observation_review_scope: str = "production-publication",
        corpus_review_scope: str = "production-publication",
    ) -> set[str]:
        batch = self.get_batch(batch_id)
        if batch is None:
            raise CorpusPersistenceError(f"corpus batch {batch_id} does not exist")
        if batch.mode != dto.CorpusMode.PRODUCTION.value:
            raise CorpusPersistenceError("production packing requires a production corpus batch")
        aggregate = self.current_decision(
            batch_id=batch_id,
            scope=dto.CorpusDecisionScope.AGGREGATE.value,
        )
        if aggregate is None or aggregate.decision != dto.CorpusDecisionValue.PASSED.value:
            return set()

        stmt = (
            sa.select(
                model.CorpusBatchMember.id,
                model.CorpusBatchMember.artifact_observation_id,
                task_model.DesignTask.challenge_id,
                observation_model.ArtifactObservation.status,
            )
            .join(
                build_model.BuildAttempt,
                build_model.BuildAttempt.id == model.CorpusBatchMember.build_attempt_id,
            )
            .join(
                task_model.DesignTask,
                task_model.DesignTask.id == build_model.BuildAttempt.design_task_id,
            )
            .join(
                observation_model.ArtifactObservation,
                observation_model.ArtifactObservation.id
                == model.CorpusBatchMember.artifact_observation_id,
            )
            .where(model.CorpusBatchMember.batch_id == batch_id)
            .order_by(model.CorpusBatchMember.created_at, model.CorpusBatchMember.id)
        )
        eligible: set[str] = set()
        for member_id, observation_id, challenge_id, observation_status in self.session.execute(stmt):
            observation_review = self.latest_observation_review(
                artifact_observation_id=observation_id,
                scope=observation_review_scope,
            )
            observation_ok = observation_status == "passed" or (
                observation_status == "inconclusive"
                and dto.observation_review_allows_acceptance(observation_review)
            )
            decision = self.current_decision(
                batch_id=batch_id,
                member_id=member_id,
                scope=dto.CorpusDecisionScope.MEMBER.value,
            )
            corpus_review = (
                self.latest_corpus_review(
                    corpus_decision_id=decision.id,
                    scope=corpus_review_scope,
                )
                if decision
                else None
            )
            decision_ok = dto.corpus_decision_is_effectively_accepted(
                decision,
                has_allowed_review=dto.corpus_review_allows_acceptance(corpus_review),
            )
            if observation_ok and decision_ok:
                eligible.add(str(challenge_id))
        return eligible

    def add_history_entry(
        self,
        *,
        challenge_id: str,
        category: str,
        fingerprint_version: int,
        fingerprints: Mapping[str, Any],
        status: str,
        audit_reason: str | None = None,
        design_evidence_id: UUID | None = None,
        build_attempt_id: UUID | None = None,
        artifact_observation_id: UUID | None = None,
        history_entry_id: UUID | None = None,
    ) -> dto.CorpusHistoryEntry:
        if status not in {item.value for item in dto.CorpusHistoryStatus}:
            raise CorpusPersistenceError(f"unknown corpus history status {status!r}")
        if fingerprint_version <= 0:
            raise CorpusPersistenceError("fingerprint_version must be positive")
        row = model.CorpusHistoryEntry(
            id=history_entry_id or uuid4(),
            challenge_id=_required_text(challenge_id, "challenge_id"),
            category=category,
            design_evidence_id=design_evidence_id,
            build_attempt_id=build_attempt_id,
            artifact_observation_id=artifact_observation_id,
            fingerprint_version=fingerprint_version,
            fingerprints=dict(fingerprints),
            status=status,
            audit_reason=audit_reason.strip() if audit_reason else None,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _history(row)

    def _lock_batch(self, batch_id: UUID) -> model.CorpusBatch:
        row = self.session.scalars(
            sa.select(model.CorpusBatch)
            .where(model.CorpusBatch.id == batch_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise CorpusPersistenceError(f"corpus batch {batch_id} does not exist")
        return row

    def _supersede_current_decision(
        self,
        *,
        batch_id: UUID,
        scope: str,
        member_id: UUID | None,
    ) -> None:
        stmt = (
            sa.select(model.CorpusDecision)
            .where(
                model.CorpusDecision.batch_id == batch_id,
                model.CorpusDecision.scope == scope,
                model.CorpusDecision.is_current.is_(True),
            )
            .with_for_update()
        )
        if member_id is None:
            stmt = stmt.where(model.CorpusDecision.member_id.is_(None))
        else:
            stmt = stmt.where(model.CorpusDecision.member_id == member_id)
        for row in self.session.scalars(stmt):
            row.is_current = False
            row.superseded_at = _utcnow()
        self.session.flush()


def _required_text(value: str, field: str) -> str:
    if not value.strip():
        raise CorpusPersistenceError(f"{field} is required")
    return value.strip()


def _token_sha(fingerprints: Mapping[str, Any], key: str) -> str:
    value = fingerprints.get(key)
    if isinstance(value, Mapping):
        return str(value.get("sha256") or "")
    return ""


def _batch(row: model.CorpusBatch) -> dto.CorpusBatch:
    return dto.CorpusBatch(
        id=row.id,
        mode=row.mode,
        category=row.category,
        policy_version=row.policy_version,
        status=row.status,
        created_by=row.created_by,
        created_at=row.created_at,
        evaluation_started_at=row.evaluation_started_at,
        evaluated_at=row.evaluated_at,
        released_at=row.released_at,
    )


def _member(row: model.CorpusBatchMember) -> dto.CorpusBatchMember:
    return dto.CorpusBatchMember(
        id=row.id,
        batch_id=row.batch_id,
        build_attempt_id=row.build_attempt_id,
        design_evidence_id=row.design_evidence_id,
        artifact_observation_id=row.artifact_observation_id,
        fingerprint_version=row.fingerprint_version,
        fingerprints=dict(row.fingerprints),
        created_at=row.created_at,
    )


def _decision(row: model.CorpusDecision) -> dto.CorpusDecision:
    return dto.CorpusDecision(
        id=row.id,
        batch_id=row.batch_id,
        member_id=row.member_id,
        scope=row.scope,
        decision=row.decision,
        reasons=tuple(str(item) for item in row.reasons),
        policy_version=row.policy_version,
        is_current=row.is_current,
        created_at=row.created_at,
        superseded_at=row.superseded_at,
    )


def _match(row: model.CorpusMatch) -> dto.CorpusMatch:
    return dto.CorpusMatch(
        id=row.id,
        batch_id=row.batch_id,
        member_id=row.member_id,
        compared_member_id=row.compared_member_id,
        compared_history_entry_id=row.compared_history_entry_id,
        fingerprint_type=row.fingerprint_type,
        score=row.score,
        threshold=row.threshold,
        reason=row.reason,
        created_at=row.created_at,
    )


def _observation_review(row: model.ObservationReviewDecision) -> dto.ObservationReviewDecision:
    return dto.ObservationReviewDecision(
        id=row.id,
        artifact_observation_id=row.artifact_observation_id,
        decision=row.decision,
        actor=row.actor,
        reason=row.reason,
        scope=row.scope,
        created_at=row.created_at,
    )


def _corpus_review(row: model.CorpusReviewDecision) -> dto.CorpusReviewDecision:
    return dto.CorpusReviewDecision(
        id=row.id,
        corpus_decision_id=row.corpus_decision_id,
        decision=row.decision,
        actor=row.actor,
        reason=row.reason,
        scope=row.scope,
        created_at=row.created_at,
    )


def _history(row: model.CorpusHistoryEntry) -> dto.CorpusHistoryEntry:
    return dto.CorpusHistoryEntry(
        id=row.id,
        challenge_id=row.challenge_id,
        category=row.category,
        design_evidence_id=row.design_evidence_id,
        build_attempt_id=row.build_attempt_id,
        artifact_observation_id=row.artifact_observation_id,
        fingerprint_version=row.fingerprint_version,
        fingerprints=dict(row.fingerprints),
        status=row.status,
        audit_reason=row.audit_reason,
        created_at=row.created_at,
    )
