"""Runtime configuration for the execution lease/fencing model.

Introduced by ``add-execution-lease-and-fencing``. Keeps the lease TTL and the
container-cutover flag in one place so the orchestration service, reconciler,
and workspace runner agree.
"""

from __future__ import annotations

import os

# Default lease lifetime. Reuses the historical build-lost grace window (300s)
# so liveness behaviour is unchanged when the model flips to leases.
DEFAULT_LEASE_TTL_SECONDS = 300


def lease_ttl_seconds() -> int:
    raw = os.environ.get("EXECUTION_LEASE_TTL_SECONDS")
    if raw is None:
        return DEFAULT_LEASE_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_LEASE_TTL_SECONDS
    return value if value > 0 else DEFAULT_LEASE_TTL_SECONDS


def execution_minting_enabled() -> bool:
    """Whether retry/clean/revision append executions (Option A) vs legacy mint.

    Defaults to **disabled** during the migration cutover window: the worker
    still claims shards from the file queue and the reconciler still mirrors
    legacy build-attempt status, so execution rows would otherwise be orphaned.
    The flag flips to enabled once the worker-claim + reaper integration
    (split-plan §5-6) lands. Operators set ``EXECUTION_MINTING=1`` to opt in.
    """

    raw = os.environ.get("EXECUTION_MINTING")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}
