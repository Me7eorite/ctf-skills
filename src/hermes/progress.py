"""Progress and report helpers for Hermes runs."""

from __future__ import annotations

import time
from pathlib import Path

from core.jsonio import read_json, write_json
from core.state import StateStore


def record_final(
    state: StateStore,
    shard: str,
    challenge_ids: list[str],
    worker: str,
    status: str,
    message: str,
) -> None:
    for challenge_id in challenge_ids:
        state.record(
            shard=shard,
            challenge_id=challenge_id,
            worker=worker,
            stage="complete",
            status=status,
            message=message,
        )
    state.record(
        shard=shard,
        worker=worker,
        stage="complete",
        status=status,
        message=message,
    )


def ensure_report(path: Path, shard: Path, worker: str, status: str, returncode: int) -> None:
    if path.exists():
        return
    write_json(
        path,
        {
            "shard": str(shard),
            "status": status,
            "worker": worker,
            "returncode": returncode,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def update_report(path: Path, status: str, error: str | None = None) -> None:
    report = read_json(path, {})
    report.update(
        {
            "runner_status": status,
            "runner_error": error,
            "runner_updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    write_json(path, report)
