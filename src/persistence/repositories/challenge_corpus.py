"""Persistence primitives for corpus-governance rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.clock import utcnow as _utcnow
from domain import challenge_corpus as dto
from persistence.models import challenge_corpus as model


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
