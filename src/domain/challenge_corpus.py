"""Corpus-governance DTOs and canonical fingerprint helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from domain.design.profile_taxonomy import (
    canonical_profile_signatures,
    taxonomy_for_category,
    validate_profile,
)

CORPUS_FINGERPRINT_SCHEMA_VERSION = 1


class CorpusMode(StrEnum):
    SHADOW = "shadow"
    TRIAL = "trial"
    PRODUCTION = "production"


class CorpusBatchStatus(StrEnum):
    DRAFT = "draft"
    EVALUATING = "evaluating"
    EVALUATED = "evaluated"
    RELEASED = "released"
    RETIRED = "retired"


class CorpusDecisionScope(StrEnum):
    MEMBER = "member"
    AGGREGATE = "aggregate"


class CorpusDecisionValue(StrEnum):
    PASSED = "passed"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class CorpusReviewDecisionValue(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ObservationReviewDecisionValue(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CorpusHistoryStatus(StrEnum):
    PUBLISHED = "published"
    RETIRED = "retired"


OBSERVATION_REVIEW_ACCEPTED_DECISION = ObservationReviewDecisionValue.ACCEPTED.value
CORPUS_REVIEW_APPROVED_DECISION = CorpusReviewDecisionValue.APPROVED.value

CorpusFingerprintType = str
CORPUS_FINGERPRINT_TYPES: tuple[CorpusFingerprintType, ...] = (
    "semantic",
    "solve",
    "implementation",
    "combined",
    "source",
    "solver",
    "intended_path",
)


@dataclass(frozen=True)
class CorpusProfileFingerprints:
    semantic: str
    solve: str
    implementation: str
    combined: str

    def as_mapping(self) -> dict[str, str]:
        return {
            "semantic": self.semantic,
            "solve": self.solve,
            "implementation": self.implementation,
            "combined": self.combined,
        }


@dataclass(frozen=True)
class TokenFingerprint:
    sha256: str
    tokens: tuple[str, ...]
    token_count: int

    def as_mapping(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "tokens": list(self.tokens),
            "token_count": self.token_count,
        }


@dataclass(frozen=True)
class CorpusFingerprints:
    schema_version: int
    profile: CorpusProfileFingerprints
    source: TokenFingerprint
    solver: TokenFingerprint
    intended_path: TokenFingerprint

    def as_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "semantic": self.profile.semantic,
            "solve": self.profile.solve,
            "implementation": self.profile.implementation,
            "combined": self.profile.combined,
            "source": self.source.as_mapping(),
            "solver": self.solver.as_mapping(),
            "intended_path": self.intended_path.as_mapping(),
        }


@dataclass(frozen=True)
class CorpusBatch:
    id: UUID
    mode: str
    category: str
    policy_version: int
    status: str
    created_by: str
    created_at: datetime
    evaluation_started_at: datetime | None
    evaluated_at: datetime | None
    released_at: datetime | None


@dataclass(frozen=True)
class CorpusBatchMember:
    id: UUID
    batch_id: UUID
    build_attempt_id: UUID
    design_evidence_id: UUID
    artifact_observation_id: UUID
    fingerprint_version: int
    fingerprints: Mapping[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class CorpusDecision:
    id: UUID
    batch_id: UUID
    member_id: UUID | None
    scope: str
    decision: str
    reasons: Sequence[str]
    policy_version: int
    is_current: bool
    created_at: datetime
    superseded_at: datetime | None


@dataclass(frozen=True)
class CorpusMatch:
    id: UUID
    batch_id: UUID
    member_id: UUID
    compared_member_id: UUID | None
    compared_history_entry_id: UUID | None
    fingerprint_type: str
    score: float
    threshold: float
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class ObservationReviewDecision:
    id: UUID
    artifact_observation_id: UUID
    decision: str
    actor: str
    reason: str
    scope: str
    created_at: datetime


@dataclass(frozen=True)
class CorpusReviewDecision:
    id: UUID
    corpus_decision_id: UUID
    decision: str
    actor: str
    reason: str
    scope: str
    created_at: datetime


@dataclass(frozen=True)
class CorpusHistoryEntry:
    id: UUID
    challenge_id: str
    category: str
    design_evidence_id: UUID | None
    build_attempt_id: UUID | None
    artifact_observation_id: UUID | None
    fingerprint_version: int
    fingerprints: Mapping[str, Any]
    status: str
    audit_reason: str | None
    created_at: datetime


@dataclass(frozen=True)
class CorpusGatePolicy:
    source_review_threshold: float = 0.45
    source_block_threshold: float = 0.65
    solver_review_threshold: float = 0.55
    solver_block_threshold: float = 0.75
    max_same_required_action_ratio: float | None = None
    max_same_flag_concealment_ratio: float | None = None
    max_same_language_ratio: float | None = None
    max_same_artifact_format_ratio: float | None = None


@dataclass(frozen=True)
class CorpusComparisonTarget:
    fingerprints: Mapping[str, Any]
    member_id: UUID | None = None
    history_entry_id: UUID | None = None
    design_task_id: UUID | None = None
    challenge_id: str | None = None


@dataclass(frozen=True)
class CorpusGateMatch:
    fingerprint_type: str
    score: float
    threshold: float
    reason: str
    compared_member_id: UUID | None = None
    compared_history_entry_id: UUID | None = None


@dataclass(frozen=True)
class CorpusGateResult:
    decision: str
    reasons: tuple[str, ...]
    matches: tuple[CorpusGateMatch, ...]


def corpus_decision_is_effectively_accepted(
    decision: CorpusDecision | None,
    *,
    has_allowed_review: bool = False,
) -> bool:
    """Return publication-layer acceptance without rewriting the stored decision."""

    if decision is None:
        return False
    if decision.decision == CorpusDecisionValue.PASSED.value:
        return True
    return (
        decision.decision == CorpusDecisionValue.REVIEW_REQUIRED.value
        and has_allowed_review
    )


def observation_review_allows_acceptance(
    review: ObservationReviewDecision | None,
) -> bool:
    return (
        review is not None
        and review.decision == OBSERVATION_REVIEW_ACCEPTED_DECISION
    )


def corpus_review_allows_acceptance(
    review: CorpusReviewDecision | None,
) -> bool:
    return review is not None and review.decision == CORPUS_REVIEW_APPROVED_DECISION


def generate_corpus_fingerprints(
    *,
    profile: Mapping[str, Any],
    category: str,
    policy_version: int,
    source_texts: Iterable[str] = (),
    solver_texts: Iterable[str] = (),
    intended_path: Iterable[str] | str | Mapping[str, Any] = (),
) -> CorpusFingerprints:
    """Generate stable corpus fingerprints for a governed challenge candidate."""

    normalized_profile = validate_profile(taxonomy_for_category(category), profile)
    signatures = canonical_profile_signatures(
        normalized_profile,
        category=category,
        policy_version=policy_version,
    )
    return CorpusFingerprints(
        schema_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
        profile=CorpusProfileFingerprints(
            semantic=signatures.semantic_signature,
            solve=signatures.solve_signature,
            implementation=signatures.implementation_signature,
            combined=signatures.combined_profile_signature,
        ),
        source=canonical_token_fingerprint(source_texts),
        solver=canonical_token_fingerprint(solver_texts),
        intended_path=canonical_token_fingerprint(_intended_path_parts(intended_path)),
    )


def evaluate_corpus_member(
    *,
    member_fingerprints: Mapping[str, Any],
    batch_targets: Sequence[CorpusComparisonTarget] = (),
    history_targets: Sequence[CorpusComparisonTarget] = (),
    mode: str = CorpusMode.PRODUCTION.value,
    observation_status: str | None = "passed",
    has_allowed_observation_review: bool = False,
    research_trial_only: bool = False,
    policy: CorpusGatePolicy | None = None,
    same_task_revision_design_task_ids: Iterable[UUID] = (),
    batch_fingerprints: Sequence[Mapping[str, Any]] = (),
) -> CorpusGateResult:
    """Evaluate one immutable corpus member against batch and history evidence."""

    if mode not in {item.value for item in CorpusMode}:
        raise ValueError(f"unknown corpus mode {mode!r}")
    active_policy = policy or CorpusGatePolicy()
    same_task_ids = set(same_task_revision_design_task_ids)
    reasons: list[str] = []
    matches: list[CorpusGateMatch] = []

    if mode == CorpusMode.PRODUCTION.value and research_trial_only:
        reasons.append("research_trial_only")

    if observation_status == "failed":
        reasons.append("artifact_observation_failed")
    elif observation_status == "inconclusive" and not has_allowed_observation_review:
        reasons.append("artifact_observation_inconclusive")
    elif observation_status not in {"passed", "inconclusive"}:
        reasons.append("artifact_observation_missing")

    for target in (*batch_targets, *history_targets):
        is_same_task_revision = (
            target.design_task_id is not None and target.design_task_id in same_task_ids
        )
        combined_match = _same_fingerprint(member_fingerprints, target.fingerprints, "combined")
        if combined_match and not is_same_task_revision:
            reasons.append("exact_combined_duplicate")
            matches.append(
                CorpusGateMatch(
                    fingerprint_type="combined",
                    score=1.0,
                    threshold=1.0,
                    reason="exact_combined_duplicate",
                    compared_member_id=target.member_id,
                    compared_history_entry_id=target.history_entry_id,
                )
            )

        for fingerprint_type, review_threshold, block_threshold in (
            (
                "source",
                active_policy.source_review_threshold,
                active_policy.source_block_threshold,
            ),
            (
                "solver",
                active_policy.solver_review_threshold,
                active_policy.solver_block_threshold,
            ),
        ):
            score = token_jaccard_score(
                _fingerprint_tokens(member_fingerprints, fingerprint_type),
                _fingerprint_tokens(target.fingerprints, fingerprint_type),
            )
            if score >= block_threshold:
                reason = f"{fingerprint_type}_similarity_block"
                reasons.append(reason)
                matches.append(
                    CorpusGateMatch(
                        fingerprint_type=fingerprint_type,
                        score=score,
                        threshold=block_threshold,
                        reason=reason,
                        compared_member_id=target.member_id,
                        compared_history_entry_id=target.history_entry_id,
                    )
                )
            elif score >= review_threshold:
                reason = f"{fingerprint_type}_similarity_review"
                reasons.append(reason)
                matches.append(
                    CorpusGateMatch(
                        fingerprint_type=fingerprint_type,
                        score=score,
                        threshold=review_threshold,
                        reason=reason,
                        compared_member_id=target.member_id,
                        compared_history_entry_id=target.history_entry_id,
                    )
                )

    quota_reasons = profile_quota_violations(
        [*batch_fingerprints, member_fingerprints],
        active_policy,
    )
    reasons.extend(quota_reasons)

    unique_reasons = tuple(dict.fromkeys(reasons))
    if any(_is_block_reason(reason, mode) for reason in unique_reasons):
        decision = CorpusDecisionValue.BLOCKED.value
    elif unique_reasons:
        decision = CorpusDecisionValue.REVIEW_REQUIRED.value
    else:
        decision = CorpusDecisionValue.PASSED.value
    if mode == CorpusMode.SHADOW.value and decision == CorpusDecisionValue.BLOCKED.value:
        decision = CorpusDecisionValue.REVIEW_REQUIRED.value
    return CorpusGateResult(decision=decision, reasons=unique_reasons, matches=tuple(matches))


def aggregate_corpus_decision(
    member_results: Sequence[CorpusGateResult | CorpusDecision],
    *,
    member_acceptance: Sequence[bool] | None = None,
) -> CorpusGateResult:
    accepted = list(member_acceptance or ())
    reasons: list[str] = []
    has_block = False
    for index, result in enumerate(member_results):
        decision = result.decision
        item_reasons = result.reasons
        accepted_by_review = accepted[index] if index < len(accepted) else False
        if decision == CorpusDecisionValue.BLOCKED.value:
            has_block = True
            reasons.extend(f"member_blocked:{reason}" for reason in item_reasons)
        elif decision == CorpusDecisionValue.REVIEW_REQUIRED.value and not accepted_by_review:
            reasons.extend(f"member_review_required:{reason}" for reason in item_reasons)
    if reasons:
        return CorpusGateResult(
            decision=CorpusDecisionValue.BLOCKED.value
            if has_block
            else CorpusDecisionValue.REVIEW_REQUIRED.value,
            reasons=tuple(dict.fromkeys(reasons)),
            matches=(),
        )
    return CorpusGateResult(
        decision=CorpusDecisionValue.PASSED.value,
        reasons=("all_members_accepted",),
        matches=(),
    )


def token_jaccard_score(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 0.0
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def profile_quota_violations(
    fingerprints: Sequence[Mapping[str, Any]],
    policy: CorpusGatePolicy,
) -> tuple[str, ...]:
    if not fingerprints:
        return ()
    total = len(fingerprints)
    checks = (
        ("solve.required_action", policy.max_same_required_action_ratio, "required_action_quota"),
        (
            "implementation.flag_concealment",
            policy.max_same_flag_concealment_ratio,
            "flag_concealment_quota",
        ),
        ("implementation.language", policy.max_same_language_ratio, "language_quota"),
        (
            "implementation.artifact_format",
            policy.max_same_artifact_format_ratio,
            "artifact_format_quota",
        ),
    )
    reasons: list[str] = []
    for path, ratio, reason in checks:
        if ratio is None:
            continue
        values = [
            value
            for item in fingerprints
            if (value := _profile_value(item, path)) not in {None, ""}
        ]
        if not values:
            continue
        cap = max(1, _ceil(total * ratio))
        if Counter(values).most_common(1)[0][1] > cap:
            reasons.append(reason)
    return tuple(reasons)


def canonical_token_fingerprint(parts: Iterable[str] | str | Mapping[str, Any]) -> TokenFingerprint:
    tokens = tuple(canonical_tokens(parts))
    return TokenFingerprint(
        sha256=_digest({"schema_version": CORPUS_FINGERPRINT_SCHEMA_VERSION, "tokens": tokens}),
        tokens=tokens,
        token_count=len(tokens),
    )


def canonical_tokens(parts: Iterable[str] | str | Mapping[str, Any]) -> tuple[str, ...]:
    text = _coerce_text(parts)
    text = _strip_comments(text)
    tokens = [
        _normalize_token(match.group(0))
        for match in _TOKEN_RE.finditer(text)
        if match.group(0).strip()
    ]
    return tuple(token for token in tokens if token)


def _intended_path_parts(value: Iterable[str] | str | Mapping[str, Any]) -> Iterable[str] | str | Mapping[str, Any]:
    return value


def _coerce_text(parts: Iterable[str] | str | Mapping[str, Any]) -> str:
    if isinstance(parts, str):
        return parts
    if isinstance(parts, Mapping):
        return json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "\n".join(str(part) for part in parts)


_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"(?m)(//|#).*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TOKEN_RE = re.compile(
    r"flag\{[^}]*\}|0x[0-9a-fA-F]+|\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_]*|==|!=|<=|>=|[-+*/%<>=&|^~!]+|[{}()[\].,:;]"
)


def _strip_comments(text: str) -> str:
    text = _HTML_COMMENT_RE.sub(" ", text)
    text = _BLOCK_COMMENT_RE.sub(" ", text)
    return _LINE_COMMENT_RE.sub(" ", text)


def _normalize_token(token: str) -> str:
    value = token.strip().lower()
    if not value:
        return ""
    if value.startswith("flag{"):
        return "<flag>"
    if value.startswith("0x"):
        return "<hex>"
    if value[0].isdigit():
        return "<num>"
    if len(value) >= 32 and re.fullmatch(r"[a-f0-9]+", value):
        return "<hexblob>"
    if value.startswith("__") and value.endswith("__"):
        return "<dunder>"
    return value


def _same_fingerprint(left: Mapping[str, Any], right: Mapping[str, Any], key: str) -> bool:
    left_value = left.get(key)
    right_value = right.get(key)
    return bool(left_value and right_value and left_value == right_value)


def _fingerprint_tokens(fingerprints: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = fingerprints.get(key)
    if isinstance(value, Mapping):
        tokens = value.get("tokens")
        if isinstance(tokens, Sequence) and not isinstance(tokens, str):
            return tuple(str(token) for token in tokens)
    if isinstance(value, str):
        return (value,)
    return ()


def _profile_value(fingerprints: Mapping[str, Any], dotted_path: str) -> str | None:
    profile = fingerprints.get("profile")
    if not isinstance(profile, Mapping):
        return None
    current: Any = profile
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return str(current) if current is not None else None


def _ceil(value: float) -> int:
    as_int = int(value)
    return as_int if as_int == value else as_int + 1


def _is_block_reason(reason: str, mode: str) -> bool:
    hard = {
        "artifact_observation_failed",
        "artifact_observation_missing",
        "artifact_observation_inconclusive",
        "exact_combined_duplicate",
        "source_similarity_block",
        "solver_similarity_block",
        "required_action_quota",
        "flag_concealment_quota",
        "language_quota",
        "artifact_format_quota",
        "research_trial_only",
    }
    if mode == CorpusMode.TRIAL.value:
        return reason in {
            "artifact_observation_failed",
            "artifact_observation_missing",
            "exact_combined_duplicate",
            "research_trial_only",
        }
    return reason in hard


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
