"""Corpus-governance DTOs and canonical fingerprint helpers."""

from __future__ import annotations

import hashlib
import json
import re
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


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
