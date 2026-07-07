"""Service for corpus admission decisions and similarity persistence."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from domain.challenge_corpus import (
    CorpusDecisionScope,
    CorpusGatePolicy,
    CorpusGateResult,
    aggregate_corpus_decision,
    corpus_decision_is_effectively_accepted,
    corpus_review_allows_acceptance,
    evaluate_corpus_member,
)
from persistence.repositories import CorpusRepository


@dataclass(frozen=True)
class CorpusEvaluationSummary:
    batch_id: UUID
    member_results: tuple[CorpusGateResult, ...]
    aggregate_result: CorpusGateResult


class ChallengeCorpusGovernanceService:
    def __init__(self, repository: CorpusRepository) -> None:
        self.repository = repository

    def evaluate_batch(
        self,
        batch_id: UUID,
        *,
        policy: CorpusGatePolicy | None = None,
        history_limit: int = 100,
        observation_review_scope: str = "production-publication",
        corpus_review_scope: str = "production-publication",
    ) -> CorpusEvaluationSummary:
        batch = self.repository.get_batch(batch_id)
        if batch is None:
            raise ValueError(f"corpus batch {batch_id} does not exist")
        if batch.status == "draft":
            batch = self.repository.start_evaluation(batch_id)

        members = self.repository.list_members(batch_id)
        results: list[CorpusGateResult] = []
        for member in members:
            batch_targets = self.repository.list_batch_comparison_targets(
                batch_id=batch_id,
                exclude_member_id=member.id,
            )
            history_targets = self.repository.list_history_shortlist(
                category=batch.category,
                fingerprints=member.fingerprints,
                limit=history_limit,
            )
            observation_review = self.repository.latest_observation_review(
                artifact_observation_id=member.artifact_observation_id,
                scope=observation_review_scope,
            )
            result = evaluate_corpus_member(
                member_fingerprints=member.fingerprints,
                batch_targets=batch_targets,
                history_targets=history_targets,
                mode=batch.mode,
                observation_status=self.repository.member_observation_status(member.id),
                has_allowed_observation_review=observation_review is not None
                and observation_review.decision == "accepted",
                research_trial_only=self.repository.member_research_trial_only(member.id),
                policy=policy,
                batch_fingerprints=[
                    target.fingerprints for target in batch_targets
                ],
            )
            for match in result.matches:
                self.repository.record_match(
                    batch_id=batch_id,
                    member_id=member.id,
                    fingerprint_type=match.fingerprint_type,
                    score=match.score,
                    threshold=match.threshold,
                    reason=match.reason,
                    compared_member_id=match.compared_member_id,
                    compared_history_entry_id=match.compared_history_entry_id,
                )
            decision = self.repository.record_decision(
                batch_id=batch_id,
                member_id=member.id,
                scope=CorpusDecisionScope.MEMBER.value,
                decision=result.decision,
                reasons=result.reasons,
                policy_version=batch.policy_version,
            )
            results.append(result)

        current_decisions = self.repository.current_member_decisions(batch_id)
        acceptance = []
        for decision in current_decisions:
            review = self.repository.latest_corpus_review(
                corpus_decision_id=decision.id,
                scope=corpus_review_scope,
            )
            acceptance.append(
                corpus_decision_is_effectively_accepted(
                    decision,
                    has_allowed_review=corpus_review_allows_acceptance(review),
                )
            )
        aggregate = aggregate_corpus_decision(current_decisions, member_acceptance=acceptance)
        aggregate_decision = self.repository.record_decision(
            batch_id=batch_id,
            scope=CorpusDecisionScope.AGGREGATE.value,
            decision=aggregate.decision,
            reasons=aggregate.reasons,
            policy_version=batch.policy_version,
        )
        aggregate_result = CorpusGateResult(
            decision=aggregate_decision.decision,
            reasons=tuple(aggregate_decision.reasons),
            matches=(),
        )
        self.repository.mark_evaluated(batch_id)
        return CorpusEvaluationSummary(
            batch_id=batch_id,
            member_results=tuple(results),
            aggregate_result=aggregate_result,
        )
