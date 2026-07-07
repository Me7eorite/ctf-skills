"""Rollout-evidence gates for evidence-backed corpus governance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from domain.challenge_corpus import CorpusDecisionValue, CorpusMode

ROLLOUT_EVIDENCE_SCHEMA_VERSION = 1
ROLLOUT_CHECKPOINTS = (20, 50, 150, 500)


@dataclass(frozen=True)
class RolloutGatePolicy:
    min_trial_batch_size: int = 20
    required_consecutive_trial_passes: int = 2
    min_member_pass_rate: float = 0.75
    max_review_rate: float = 0.25
    max_blocked_duplicate_rate: float = 0.0


def build_rollout_evidence(
    *,
    shadow_report: Mapping[str, Any],
    trial_reports: Sequence[Mapping[str, Any]],
    policy: RolloutGatePolicy | None = None,
) -> dict[str, Any]:
    """Normalize rollout reports and evaluate the production-mode gate."""

    active_policy = policy or RolloutGatePolicy()
    shadow = _evaluate_shadow_report(shadow_report)
    trials = [
        _evaluate_trial_report(report, active_policy)
        for report in trial_reports
    ]
    consecutive_passes = _count_consecutive_passes(trials)
    cumulative_passed = sum(
        int(trial["challenge_count"]) for trial in trials if trial["passed"]
    )
    gate_reasons: list[str] = []
    if not shadow["reported"]:
        gate_reasons.append("shadow_current_corpus_report_missing")
    if consecutive_passes < active_policy.required_consecutive_trial_passes:
        gate_reasons.append("insufficient_consecutive_trial_passes")

    production_allowed = not gate_reasons
    return {
        "schema_version": ROLLOUT_EVIDENCE_SCHEMA_VERSION,
        "status": "production_gate_passed"
        if production_allowed
        else "production_gate_blocked",
        "production_mode_allowed": production_allowed,
        "production_mode_action": "manual_enable_allowed"
        if production_allowed
        else "keep_disabled",
        "shadow_current_corpus": shadow,
        "trial_batches": trials,
        "acceptance_metrics": _acceptance_metrics(trials),
        "rollout_gate": {
            "required_consecutive_trial_passes": (
                active_policy.required_consecutive_trial_passes
            ),
            "consecutive_passed_trial_batches": consecutive_passes,
            "cumulative_passed_trial_challenges": cumulative_passed,
            "next_checkpoint": _next_checkpoint(cumulative_passed),
            "reasons": gate_reasons,
        },
        "policy": {
            "min_trial_batch_size": active_policy.min_trial_batch_size,
            "required_consecutive_trial_passes": (
                active_policy.required_consecutive_trial_passes
            ),
            "min_member_pass_rate": active_policy.min_member_pass_rate,
            "max_review_rate": active_policy.max_review_rate,
            "max_blocked_duplicate_rate": active_policy.max_blocked_duplicate_rate,
        },
    }


def _evaluate_shadow_report(report: Mapping[str, Any]) -> dict[str, Any]:
    required_vs_observed = _count_mapping(report.get("required_vs_observed"))
    similarity = _decision_counts(report)
    total = _int_value(report, "challenge_count", default=0) or _total_count(
        required_vs_observed
    ) or _total_count(similarity)
    published = total > 0 and bool(required_vs_observed or similarity)
    return {
        "id": str(report.get("id") or report.get("batch_id") or "current-corpus"),
        "mode": str(report.get("mode") or CorpusMode.SHADOW.value),
        "reported": published,
        "challenge_count": total,
        "required_vs_observed": required_vs_observed,
        "similarity": similarity,
        "report_uri": str(report.get("report_uri") or ""),
    }


def _evaluate_trial_report(
    report: Mapping[str, Any],
    policy: RolloutGatePolicy,
) -> dict[str, Any]:
    challenge_count = _int_value(report, "challenge_count", default=0)
    decisions = _decision_counts(report)
    passed_members = decisions.get(CorpusDecisionValue.PASSED.value, 0)
    review_members = decisions.get(CorpusDecisionValue.REVIEW_REQUIRED.value, 0)
    if challenge_count <= 0:
        challenge_count = max(_total_count(decisions), _int_value(report, "total", default=0))

    design_evidence_passed = _int_value(
        report,
        "design_evidence_passed",
        default=_nested_int(report, "design_evidence", "passed"),
    )
    build_contracts_passed = _int_value(
        report,
        "build_contracts_passed",
        default=_nested_int(report, "build_contracts", "passed"),
    )
    observations_passed = _int_value(
        report,
        "artifact_observations_passed",
        default=_nested_int(report, "artifact_observations", "passed"),
    )
    blocked_duplicates = _int_value(
        report,
        "blocked_duplicate_count",
        default=_nested_int(report, "blocked_duplicates", "count"),
    )

    pass_rate = _rate(passed_members, challenge_count)
    review_rate = _rate(review_members, challenge_count)
    blocked_duplicate_rate = _rate(blocked_duplicates, challenge_count)
    aggregate_decision = str(
        report.get("aggregate_decision") or CorpusDecisionValue.BLOCKED.value
    )
    reasons: list[str] = []
    if str(report.get("mode") or CorpusMode.TRIAL.value) != CorpusMode.TRIAL.value:
        reasons.append("not_trial_mode")
    if challenge_count < policy.min_trial_batch_size:
        reasons.append("trial_batch_below_20")
    if design_evidence_passed != challenge_count:
        reasons.append("design_evidence_not_all_passed")
    if build_contracts_passed != challenge_count:
        reasons.append("build_contracts_not_all_passed")
    if observations_passed != challenge_count:
        reasons.append("artifact_observations_not_all_passed")
    if aggregate_decision != CorpusDecisionValue.PASSED.value:
        reasons.append("aggregate_not_passed")
    if pass_rate < policy.min_member_pass_rate:
        reasons.append("pass_rate_below_threshold")
    if review_rate > policy.max_review_rate:
        reasons.append("review_rate_above_threshold")
    if blocked_duplicate_rate > policy.max_blocked_duplicate_rate:
        reasons.append("blocked_duplicate_rate_above_threshold")

    return {
        "id": str(report.get("id") or report.get("batch_id") or ""),
        "mode": str(report.get("mode") or CorpusMode.TRIAL.value),
        "challenge_count": challenge_count,
        "difficulty_distribution": dict(report.get("difficulty_distribution") or {}),
        "profile_distribution": dict(report.get("profile_distribution") or {}),
        "design_evidence_passed": design_evidence_passed,
        "build_contracts_passed": build_contracts_passed,
        "artifact_observations_passed": observations_passed,
        "aggregate_decision": aggregate_decision,
        "member_decisions": decisions,
        "metrics": {
            "pass_rate": pass_rate,
            "review_rate": review_rate,
            "blocked_duplicate_rate": blocked_duplicate_rate,
            "blocked_duplicate_count": blocked_duplicates,
            "false_positive_review_findings": _int_value(
                report,
                "false_positive_review_findings",
                default=0,
            ),
        },
        "passed": not reasons,
        "reasons": reasons,
        "report_uri": str(report.get("report_uri") or ""),
    }


def _acceptance_metrics(trials: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = sum(int(trial.get("challenge_count") or 0) for trial in trials)
    passed = sum(
        int(trial["member_decisions"].get(CorpusDecisionValue.PASSED.value, 0))
        for trial in trials
    )
    reviews = sum(
        int(trial["member_decisions"].get(CorpusDecisionValue.REVIEW_REQUIRED.value, 0))
        for trial in trials
    )
    duplicate_blocks = sum(
        int(trial["metrics"].get("blocked_duplicate_count", 0)) for trial in trials
    )
    false_positive_reviews = sum(
        int(trial["metrics"].get("false_positive_review_findings", 0))
        for trial in trials
    )
    return {
        "challenge_count": total,
        "pass_rate": _rate(passed, total),
        "review_rate": _rate(reviews, total),
        "blocked_duplicate_rate": _rate(duplicate_blocks, total),
        "blocked_duplicate_count": duplicate_blocks,
        "false_positive_review_findings": false_positive_reviews,
        "profile_distribution": _merge_profile_distribution(trials),
    }


def _decision_counts(report: Mapping[str, Any]) -> dict[str, int]:
    raw = report.get("member_decisions") or report.get("decisions") or {}
    counts = _count_mapping(raw)
    return {
        CorpusDecisionValue.PASSED.value: counts.get(CorpusDecisionValue.PASSED.value, 0),
        CorpusDecisionValue.REVIEW_REQUIRED.value: counts.get(
            CorpusDecisionValue.REVIEW_REQUIRED.value,
            0,
        ),
        CorpusDecisionValue.BLOCKED.value: counts.get(CorpusDecisionValue.BLOCKED.value, 0),
    }


def _count_mapping(raw: Any) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): int(value) for key, value in raw.items() if int(value) >= 0}


def _int_value(report: Mapping[str, Any], key: str, *, default: int = 0) -> int:
    value = report.get(key, default)
    return int(value) if value not in {None, ""} else default


def _nested_int(report: Mapping[str, Any], key: str, nested_key: str) -> int:
    value = report.get(key)
    if not isinstance(value, Mapping):
        return 0
    return _int_value(value, nested_key, default=0)


def _total_count(counts: Mapping[str, int]) -> int:
    return sum(int(value) for value in counts.values())


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _count_consecutive_passes(trials: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for trial in reversed(trials):
        if not trial.get("passed"):
            break
        count += 1
    return count


def _next_checkpoint(cumulative_passed: int) -> int | None:
    for checkpoint in ROLLOUT_CHECKPOINTS:
        if cumulative_passed < checkpoint:
            return checkpoint
    return None


def _merge_profile_distribution(trials: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for trial in trials:
        distribution = trial.get("profile_distribution") or {}
        if not isinstance(distribution, Mapping):
            continue
        for key, value in distribution.items():
            merged[str(key)] = merged.get(str(key), 0) + int(value)
    return merged
