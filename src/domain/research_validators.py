"""Domain-level validators for the research-planning workflow.

These functions enforce structural rules that can be checked without a
database round trip (distribution sums, label whitelists, finding-source
cardinality). Cross-row checks that need a SELECT (e.g. "this source_id
belongs to this run") live in the repository layer and raise the same
``ResearchValidationError`` type for caller convenience.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from domain.research import DIFFICULTY_LABELS, ResearchFindingKind


class ResearchValidationError(ValueError):
    """Raised when a domain validator rejects input."""


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
    require a database query.
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
