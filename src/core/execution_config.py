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

    Defaults to disabled so legacy build-attempt minting remains the baseline.
    Operators may set ``EXECUTION_MINTING=1`` to enable the execution-backed
    cutover path.
    """

    raw = os.environ.get("EXECUTION_MINTING")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}
