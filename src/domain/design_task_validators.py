"""Domain-level validators for the design-task-planning workflow.

Mirrors the shape rules that ``SeedStore.validate_seed`` already
enforces for file-backed shards (``domain.seeds.validate_seed``), so the
first database-backed design-task layer cannot diverge from the old
shard seed format. Cross-row checks that need a SELECT (e.g. "this
finding belongs to the same research_run") live in the planning service
and raise the same ``DesignTaskValidationError``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from domain.design_tasks import DesignTaskStatus
from domain.research import DIFFICULTY_LABELS

REQUIRED_TEXT_FIELDS: tuple[str, ...] = (
    "challenge_id",
    "title",
    "category",
    "difficulty",
    "primary_technique",
    "learning_objective",
)


class DesignTaskValidationError(ValueError):
    """Raised when a design-task candidate or status transition is invalid."""


def validate_candidate(
    candidate: Mapping[str, Any],
    *,
    parent_category: str,
    task_no: int,
) -> None:
    """Reject a single design-task candidate that is not shard-compatible.

    Checks shard seed required-field non-emptiness, that ``category``
    equals the parent request category, that ``challenge_id`` matches
    the ``{category}-` prefix rule, that ``difficulty`` is one of
    ``DIFFICULTY_LABELS``, that ``points`` is positive, that ``port`` is
    a valid TCP port for web/pwn, and that ``task_no`` matches the
    expected per-request sequence number.
    """
    for field in REQUIRED_TEXT_FIELDS:
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise DesignTaskValidationError(
                f"task field {field!r} must be a non-empty string"
            )

    category = candidate["category"]
    if category != parent_category:
        raise DesignTaskValidationError(
            f"task category {category!r} does not match parent request "
            f"category {parent_category!r}"
        )

    challenge_id = candidate["challenge_id"]
    if not challenge_id.startswith(f"{category}-"):
        raise DesignTaskValidationError(
            f"challenge_id {challenge_id!r} prefix must match category "
            f"{category!r}"
        )

    difficulty = candidate["difficulty"]
    if difficulty not in DIFFICULTY_LABELS:
        raise DesignTaskValidationError(
            f"difficulty {difficulty!r} is not allowed; "
            f"allowed: {list(DIFFICULTY_LABELS)}"
        )

    points = candidate.get("points")
    if not isinstance(points, int) or isinstance(points, bool) or points <= 0:
        raise DesignTaskValidationError("points must be a positive integer")

    port = candidate.get("port")
    if category in {"web", "pwn"}:
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise DesignTaskValidationError(
                "port must be a valid TCP port (1-65535) for web/pwn tasks"
            )
    elif port is not None and (
        not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
    ):
        raise DesignTaskValidationError(
            "port must be null or a valid TCP port (1-65535)"
        )

    candidate_task_no = candidate.get("task_no")
    if candidate_task_no != task_no:
        raise DesignTaskValidationError(
            f"task_no {candidate_task_no!r} does not match expected {task_no}"
        )

    finding_ids = candidate.get("finding_ids")
    # planner output may not include any finding citation; the planning
    # service does the cross-row "belongs to this run" check after this.
    if finding_ids is not None and not isinstance(finding_ids, (list, tuple)):
        raise DesignTaskValidationError("finding_ids must be a list of UUIDs")


def validate_candidate_set(
    candidates: Sequence[Mapping[str, Any]],
    *,
    target_count: int,
    difficulty_distribution: Mapping[str, int],
) -> None:
    """Reject a candidate set whose size or difficulty mix is wrong."""
    if len(candidates) != target_count:
        raise DesignTaskValidationError(
            f"generated {len(candidates)} task(s) but target_count is {target_count}"
        )
    seen_task_nos = sorted(int(c.get("task_no", 0)) for c in candidates)
    expected = list(range(1, target_count + 1))
    if seen_task_nos != expected:
        raise DesignTaskValidationError(
            f"task_no sequence {seen_task_nos} does not equal {expected}"
        )
    actual_distribution: dict[str, int] = {}
    for candidate in candidates:
        difficulty = candidate.get("difficulty")
        if not isinstance(difficulty, str):
            continue
        actual_distribution[difficulty] = actual_distribution.get(difficulty, 0) + 1
    expected_distribution = {
        label: int(count) for label, count in difficulty_distribution.items() if count
    }
    if actual_distribution != expected_distribution:
        raise DesignTaskValidationError(
            f"difficulty mix {actual_distribution} does not match "
            f"{expected_distribution}"
        )


def validate_status_transition(current: str, target: str) -> None:
    """Reject a planning-side status transition that is not operator-allowed.

    This change implements only the operator transitions
    ``draft -> queued``, ``draft -> archived``, and
    ``queued -> archived``. Worker transitions
    (``queued -> designing -> designed`` / ``failed``) are reserved for
    the future design-worker change and rejected here.
    """
    if current not in DesignTaskStatus:
        raise DesignTaskValidationError(
            f"current status {current!r} is not a valid design task status"
        )
    if target not in DesignTaskStatus:
        raise DesignTaskValidationError(
            f"target status {target!r} is not a valid design task status"
        )
    allowed: dict[str, frozenset[str]] = {
        "draft": frozenset({"queued", "archived"}),
        "queued": frozenset({"archived"}),
    }
    if target not in allowed.get(current, frozenset()):
        raise DesignTaskValidationError(
            f"transition {current!r} -> {target!r} is not allowed by the "
            "planning endpoint"
        )
