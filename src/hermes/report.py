"""Report merge helpers for Hermes runner output."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.jsonio import read_json, write_json


def merge_validation_into_report(
    report: Path,
    per_results: list[dict[str, Any]],
    *,
    shard: Path | None = None,
    worker: str | None = None,
    runner_status: str | None = None,
) -> None:
    """Merge per-challenge validation results into the shard report.

    Repairs malformed Hermes-written report structures rather than dropping
    validation outcomes. ``shard`` / ``worker`` / ``runner_status`` are only
    used when a report file does not yet exist (for example in the all-skipped
    short-circuit path).
    """
    raw = read_json(report, {})
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("challenges"), list):
        raw["challenges"] = []
    challenges_list = raw["challenges"]

    by_id: dict[str, dict[str, Any]] = {}
    for entry in challenges_list:
        if isinstance(entry, dict):
            challenge_id = entry.get("id") or entry.get("challenge_id")
            if isinstance(challenge_id, str):
                by_id[challenge_id] = entry

    any_failed = False
    for result in per_results:
        challenge_id = result["challenge_id"]
        target = by_id.get(challenge_id)
        if target is None:
            target = {"id": challenge_id}
            challenges_list.append(target)
        target.setdefault("id", challenge_id)
        target["solve_status"] = result.get("solve_status", "failed")
        target["validation_status"] = result.get(
            "validation_status", target.get("validation_status", "")
        )
        if "validation_elapsed" in result:
            target["validation_elapsed"] = result["validation_elapsed"]
        if "validation_error" in result:
            target["validation_error"] = result["validation_error"]
        if target["solve_status"] == "failed":
            any_failed = True

    if shard is not None:
        raw.setdefault("shard", str(shard))
    if worker is not None:
        raw.setdefault("worker", worker)
    if runner_status is not None:
        raw["runner_status"] = "failed" if any_failed else runner_status

    write_json(report, raw)
