"""Per-stage duration metrics computed from progress events.

Reads only the latest claim window for a given original shard name and
produces wall-clock end-to-end durations per stage. A stage duration is
``last_passed.created_at - first_running.created_at`` and is present only
when the latest event for the stage in the window is ``passed`` and the
window also contains a ``running`` event for that stage. Carry-forward
``passed`` events without a corresponding ``running`` event yield ``None``.
"""

from __future__ import annotations

import time
from typing import Any

from core.state import StateStore

STAGE_ORDER: tuple[str, ...] = (
    "design",
    "implement",
    "build",
    "validate",
    "document",
)

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_timestamp(value: str) -> float | None:
    try:
        return time.mktime(time.strptime(value, _TIMESTAMP_FORMAT))
    except (TypeError, ValueError):
        return None


def _latest_event(events: list[dict[str, Any]], stage: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("stage") == stage:
            return event
    return None


def _first_running_event(
    events: list[dict[str, Any]], stage: str
) -> dict[str, Any] | None:
    for event in events:
        if event.get("stage") == stage and event.get("status") == "running":
            return event
    return None


def duration_breakdown(
    state: StateStore, challenge_id: str, shard: str
) -> dict[str, float | None]:
    """Return durations for the five stages in the latest claim window.

    ``shard`` MUST be the normalized original basename. The window starts at
    the latest shard-level ``queued/running`` event for ``shard`` and includes
    every challenge event from that event onward.
    """
    latest_claim = state.latest_claim_event(shard)
    if latest_claim is None:
        return {stage: None for stage in STAGE_ORDER}

    events = state.events_for_challenge(
        shard, challenge_id, after_id=int(latest_claim["id"])
    )

    durations: dict[str, float | None] = {}
    for stage in STAGE_ORDER:
        latest = _latest_event(events, stage)
        if latest is None or latest.get("status") != "passed":
            durations[stage] = None
            continue
        running = _first_running_event(events, stage)
        if running is None:
            # Carry-forward passed without running -> no measurable duration.
            durations[stage] = None
            continue
        start_ts = _parse_timestamp(str(running.get("created_at", "")))
        end_ts = _parse_timestamp(str(latest.get("created_at", "")))
        if start_ts is None or end_ts is None:
            durations[stage] = None
            continue
        durations[stage] = max(0.0, end_ts - start_ts)
    return durations
