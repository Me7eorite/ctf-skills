"""Unit tests for corpus-governance service orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from domain.challenge_corpus import (
    CorpusBatch,
    CorpusBatchMember,
    CorpusBatchStatus,
    CorpusComparisonTarget,
    CorpusDecision,
    CorpusDecisionScope,
    CorpusDecisionValue,
    CorpusMode,
    canonical_token_fingerprint,
)
from services.challenge_corpus_governance import ChallengeCorpusGovernanceService


class FakeCorpusRepository:
    def __init__(self) -> None:
        self.batch_id = uuid4()
        self.member_id = uuid4()
        self.batch = CorpusBatch(
            id=self.batch_id,
            mode=CorpusMode.PRODUCTION.value,
            category="web",
            policy_version=1,
            status=CorpusBatchStatus.DRAFT.value,
            created_by="operator",
            created_at=datetime.now(timezone.utc),
            evaluation_started_at=None,
            evaluated_at=None,
            released_at=None,
        )
        self.member = CorpusBatchMember(
            id=self.member_id,
            batch_id=self.batch_id,
            build_attempt_id=uuid4(),
            design_evidence_id=uuid4(),
            artifact_observation_id=uuid4(),
            fingerprint_version=1,
            fingerprints={
                "combined": "candidate",
                "source": canonical_token_fingerprint("alpha beta gamma").as_mapping(),
                "solver": canonical_token_fingerprint("solver unique").as_mapping(),
            },
            created_at=datetime.now(timezone.utc),
        )
        self.decisions: list[CorpusDecision] = []
        self.matches: list[dict[str, object]] = []
        self.evaluated = False

    def get_batch(self, batch_id):
        assert batch_id == self.batch_id
        return self.batch

    def start_evaluation(self, batch_id):
        assert batch_id == self.batch_id
        self.batch = CorpusBatch(
            **{**self.batch.__dict__, "status": CorpusBatchStatus.EVALUATING.value}
        )
        return self.batch

    def mark_evaluated(self, batch_id):
        assert batch_id == self.batch_id
        self.evaluated = True
        self.batch = CorpusBatch(
            **{**self.batch.__dict__, "status": CorpusBatchStatus.EVALUATED.value}
        )
        return self.batch

    def list_members(self, batch_id):
        assert batch_id == self.batch_id
        return [self.member]

    def list_batch_comparison_targets(self, *, batch_id, exclude_member_id=None):
        assert batch_id == self.batch_id
        assert exclude_member_id == self.member_id
        return []

    def list_history_shortlist(self, *, category, fingerprints, limit=100):
        assert category == "web"
        return [
            CorpusComparisonTarget(
                history_entry_id=uuid4(),
                fingerprints={
                    "combined": "history",
                    "source": canonical_token_fingerprint("alpha beta delta").as_mapping(),
                    "solver": canonical_token_fingerprint("solver other").as_mapping(),
                },
            )
        ]

    def latest_observation_review(self, *, artifact_observation_id, scope=None):
        return None

    def member_observation_status(self, member_id):
        assert member_id == self.member_id
        return "passed"

    def member_research_trial_only(self, member_id):
        assert member_id == self.member_id
        return False

    def record_match(self, **kwargs):
        self.matches.append(kwargs)

    def record_decision(self, **kwargs):
        decision = CorpusDecision(
            id=uuid4(),
            batch_id=kwargs["batch_id"],
            member_id=kwargs.get("member_id"),
            scope=kwargs["scope"],
            decision=kwargs["decision"],
            reasons=tuple(kwargs["reasons"]),
            policy_version=kwargs["policy_version"],
            is_current=True,
            created_at=datetime.now(timezone.utc),
            superseded_at=None,
        )
        self.decisions.append(decision)
        return decision

    def current_member_decisions(self, batch_id):
        assert batch_id == self.batch_id
        return [
            decision
            for decision in self.decisions
            if decision.scope == CorpusDecisionScope.MEMBER.value
        ]

    def latest_corpus_review(self, *, corpus_decision_id, scope=None):
        return None


def test_evaluate_batch_records_matches_member_decision_and_aggregate() -> None:
    repo = FakeCorpusRepository()
    service = ChallengeCorpusGovernanceService(repo)  # type: ignore[arg-type]

    summary = service.evaluate_batch(repo.batch_id)

    assert repo.evaluated is True
    assert repo.matches
    assert repo.decisions[0].scope == CorpusDecisionScope.MEMBER.value
    assert repo.decisions[0].decision == CorpusDecisionValue.REVIEW_REQUIRED.value
    assert repo.decisions[1].scope == CorpusDecisionScope.AGGREGATE.value
    assert summary.aggregate_result.decision == CorpusDecisionValue.REVIEW_REQUIRED.value
