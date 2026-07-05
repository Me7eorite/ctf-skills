"""Build hard-timeout policy shared by runner, CLI dispatch, and Web UI."""

from __future__ import annotations

import math
import time
from typing import Any, Mapping

from core.queue import SUPPORTED_CATEGORIES

VALIDATION_REPAIR_TIMEOUT_CAP = 600
GLOBAL_DEADLINE_PHASE = "global_deadline_exceeded"


class AttemptDeadlineExceeded(TimeoutError):
    """Raised when a build attempt reaches its non-renewable global deadline."""


def deadline_from_timeout(timeout_seconds: int | float | None) -> float | None:
    """Return a monotonic deadline for a fresh attempt timeout budget."""
    if timeout_seconds is None:
        return None
    if timeout_seconds <= 0:
        raise ValueError("attempt timeout must be positive")
    return time.monotonic() + float(timeout_seconds)


def deadline_from_epoch(deadline_epoch: int | float | None) -> float | None:
    """Convert a wall-clock epoch deadline into this process's monotonic clock."""
    if deadline_epoch is None:
        return None
    return time.monotonic() + (float(deadline_epoch) - time.time())


def remaining_attempt_time(attempt_deadline: float | None) -> float | None:
    """Seconds until the attempt deadline, or None when no deadline is active."""
    if attempt_deadline is None:
        return None
    return float(attempt_deadline) - time.monotonic()


def attempt_deadline_expired(attempt_deadline: float | None) -> bool:
    remaining = remaining_attempt_time(attempt_deadline)
    return remaining is not None and remaining <= 0


def bounded_hermes_timeout(
    configured_timeout: int | float,
    attempt_deadline: float | None,
) -> float:
    """Clamp one Hermes call to the remaining attempt deadline."""
    if configured_timeout <= 0:
        raise ValueError("configured timeout must be positive")
    remaining = remaining_attempt_time(attempt_deadline)
    if remaining is None:
        return configured_timeout
    if remaining <= 0:
        raise AttemptDeadlineExceeded("global deadline exceeded")
    return max(0.001, min(float(configured_timeout), remaining))


def deadline_metadata(
    *,
    attempt_timeout_seconds: int | float | None,
    attempt_deadline: float | None,
    started_monotonic: float,
) -> dict[str, Any]:
    """Stable metadata for attempt-deadline terminal outcomes."""
    elapsed = max(0.0, time.monotonic() - started_monotonic)
    metadata: dict[str, Any] = {
        "timeout_kind": "attempt_deadline",
        "elapsed_seconds": elapsed,
    }
    if attempt_timeout_seconds is not None:
        metadata["attempt_timeout_seconds"] = attempt_timeout_seconds
    if attempt_deadline is not None:
        wall_deadline = time.time() + (attempt_deadline - time.monotonic())
        metadata["deadline_at"] = wall_deadline
        metadata["deadline_at_epoch"] = wall_deadline
    return metadata


def attempt_timeout_outcome(
    *,
    shard: str,
    attempt_timeout_seconds: int | float | None,
    attempt_deadline: float | None,
    started_monotonic: float,
) -> dict[str, Any]:
    """Return the canonical failed outcome for a global attempt timeout."""
    outcome = {
        "status": "failed",
        "shard": shard,
        "hermes_phase": GLOBAL_DEADLINE_PHASE,
        "validation_status": "timeout",
        "build_status": "timeout",
        "error": "global deadline exceeded",
    }
    outcome.update(
        deadline_metadata(
            attempt_timeout_seconds=attempt_timeout_seconds,
            attempt_deadline=attempt_deadline,
            started_monotonic=started_monotonic,
        )
    )
    if outcome.get("attempt_timeout_seconds") is not None:
        outcome["attempt_timeout_seconds"] = int(
            math.ceil(float(outcome["attempt_timeout_seconds"]))
        )
    return outcome


def shard_timeout_policy(payload: Mapping[str, Any]) -> int:
    challenges = payload.get("challenges")
    if not isinstance(challenges, list) or not challenges:
        raise ValueError("shard challenges must be a non-empty array")
    categories = {
        item.get("category") for item in challenges if isinstance(item, dict)
    }
    if len(categories) != 1:
        raise ValueError("all shard challenges must use one category")
    category = next(iter(categories))
    if category not in SUPPORTED_CATEGORIES:
        raise ValueError(f"unsupported challenge category: {category!r}")
    if category == "re":
        return 1800
    if category == "web":
        return 2700
    if any(
        isinstance(item, dict) and item.get("difficulty") == "expert"
        for item in challenges
    ):
        return 5400
    return 3600


def validation_repair_timeout_cap() -> int:
    """Return the default upper bound applied to one validation repair round."""
    return VALIDATION_REPAIR_TIMEOUT_CAP
