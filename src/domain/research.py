"""Domain DTOs and validators for the research-planning workflow.

Mirrors the eight tables introduced by Alembic revision
``0002_research_tables``. DTOs are frozen dataclasses; the allowed value
sets for the three PG enum columns plus the binding-status CHECK are
exposed as tuple constants. The category and role sets are NOT hardcoded
here — those are lookup tables and the repository queries them at
runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# ---------------------------------------------------------------------------
# Allowed-value sets for the three PG enum types and the binding status
# CHECK. Mirror the migration; keep as tuples so they have a stable order
# usable in error messages.
# ---------------------------------------------------------------------------

GenerationRequestStatus: tuple[str, ...] = (
    "draft",
    "researching",
    "researched",
    "failed",
)
ResearchRunStatus: tuple[str, ...] = (
    "queued",
    "running",
    "completed",
    "failed",
)
ResearchFindingKind: tuple[str, ...] = (
    "technique",
    "variant",
    "scenario",
    "prerequisite",
)
BindingStatus: tuple[str, ...] = ("enabled", "disabled")
DIFFICULTY_LABELS: tuple[str, ...] = ("easy", "medium", "hard", "expert")


# ---------------------------------------------------------------------------
# Frozen dataclass DTOs. They are the in-memory view of the eight tables.
# Frozen is enforced at the field-reassignment level; mutable values like
# dicts inside a row are still mutable by convention.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChallengeCategory:
    code: str
    display_name: str
    description: str | None = None


@dataclass(frozen=True)
class AgentRole:
    code: str
    display_name: str
    description: str | None = None


@dataclass(frozen=True)
class HermesProfileBinding:
    role: str
    profile_name: str
    description: str | None
    status: str
    last_used_at: datetime | None
    last_used_run_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class GenerationRequest:
    id: UUID
    category: str
    topic: str
    target_count: int
    difficulty_distribution: Mapping[str, int]
    runtime_constraints: Mapping[str, Any]
    seed_urls: tuple[str, ...]
    max_attempts: int
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ResearchRun:
    id: UUID
    generation_request_id: UUID
    parent_run_id: UUID | None
    attempt: int
    status: str
    claimed_by: str | None
    claim_token: UUID | None
    claimed_at: datetime | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    hermes_log_path: str | None
    profile_name_used: str | None
    created_at: datetime


@dataclass(frozen=True)
class ResearchSource:
    id: UUID
    research_run_id: UUID
    url: str
    title: str
    summary: str
    content_hash: str
    fetched_at: datetime
    raw_text_path: str | None = None


@dataclass(frozen=True)
class ResearchFinding:
    id: UUID
    research_run_id: UUID
    kind: str
    label: str
    summary: str


@dataclass(frozen=True)
class ResearchFindingSource:
    finding_id: UUID
    source_id: UUID


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


class ResearchValidationError(ValueError):
    """Raised when a domain validator rejects input.

    Use this for any validation that is enforceable without a database round
    trip: distribution sums, label whitelists, finding-source cardinality.
    Cross-row checks (e.g. "this source_id belongs to this run") are the
    repository's responsibility and surface as the same error type.
    """


def validate_distribution(
    target_count: int, distribution: Mapping[str, int]
) -> None:
    """Reject an invalid difficulty distribution.

    Rules: every label must be in ``DIFFICULTY_LABELS``; counts must be
    non-negative integers; the sum must equal ``target_count``.
    """
    if target_count <= 0:
        raise ResearchValidationError(
            f"target_count must be positive, got {target_count}"
        )
    if not distribution:
        raise ResearchValidationError(
            "difficulty_distribution is empty; "
            f"expected sum to equal target_count={target_count}"
        )
    unknown = sorted(label for label in distribution if label not in DIFFICULTY_LABELS)
    if unknown:
        raise ResearchValidationError(
            f"unknown difficulty label(s) {unknown}; "
            f"allowed: {list(DIFFICULTY_LABELS)}"
        )
    negatives = sorted(label for label, count in distribution.items() if count < 0)
    if negatives:
        raise ResearchValidationError(
            f"difficulty counts must be non-negative; negative for: {negatives}"
        )
    total = sum(distribution.values())
    if total != target_count:
        raise ResearchValidationError(
            f"difficulty_distribution sums to {total} but target_count is {target_count}"
        )


def validate_category(category: str | None, allowed_codes: Iterable[str]) -> None:
    """Reject a missing or unknown challenge category.

    ``allowed_codes`` is supplied by the caller (typically the repository
    after a ``SELECT code FROM challenge_categories``), because the source
    of truth is the lookup table — not a Python constant.
    """
    if not category:
        raise ResearchValidationError("category is required; got missing/empty value")
    allowed = set(allowed_codes)
    if category not in allowed:
        raise ResearchValidationError(
            f"category {category!r} is not allowed; "
            f"allowed: {sorted(allowed)}"
        )


def validate_finding(kind: str, source_ids: Sequence[UUID]) -> None:
    """Reject a finding without sources or with duplicate sources.

    Cross-run checks (each ``source_id`` must belong to the same
    ``research_run_id`` as the finding) live in the repository because they
    require a database query; the spec covers them under the repository's
    ``create_finding`` invariant.
    """
    if kind not in ResearchFindingKind:
        raise ResearchValidationError(
            f"finding kind {kind!r} is not allowed; "
            f"allowed: {list(ResearchFindingKind)}"
        )
    if not source_ids:
        raise ResearchValidationError(
            "finding must reference at least one source (source_ids is empty)"
        )
    seen: set[UUID] = set()
    duplicates: list[UUID] = []
    for sid in source_ids:
        if sid in seen and sid not in duplicates:
            duplicates.append(sid)
        seen.add(sid)
    if duplicates:
        raise ResearchValidationError(
            f"finding source_ids contain duplicate(s): {duplicates}"
        )
