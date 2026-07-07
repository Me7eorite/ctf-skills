"""Small service-side normalizations for model-shaped design payloads."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from domain.design.difficulty import RUBRIC
from domain.design_tasks import DesignTask


def normalize_design_payload_for_task(
    payload: Mapping[str, Any],
    parent_task: DesignTask,
) -> dict[str, Any]:
    """Normalize common Hermes drift before strict domain validation.

    The validator remains authoritative. This function only handles narrow,
    deterministic shape drift that preserves the design intent, currently the
    common easy-tier over-budget cases: extra declared techniques and padded
    solve-path steps.
    """
    normalized = copy.deepcopy(dict(payload))
    challenges = normalized.get("challenges")
    if not isinstance(challenges, list) or not challenges:
        return normalized
    challenge = challenges[0]
    if not isinstance(challenge, dict):
        return normalized

    if parent_task.difficulty == "easy":
        _normalize_easy_budget(challenge)

    return normalized


def _normalize_easy_budget(challenge: dict[str, Any]) -> None:
    rubric = RUBRIC["easy"]
    _collapse_easy_techniques(challenge)
    _trim_intended_path(challenge, rubric.intended_path_max)


def _collapse_easy_techniques(challenge: dict[str, Any]) -> None:
    primary = challenge.get("primary_technique")
    if not isinstance(primary, str) or not primary.strip():
        techniques = challenge.get("techniques")
        if isinstance(techniques, list):
            primary = next(
                (item for item in techniques if isinstance(item, str) and item.strip()),
                "",
            )
        else:
            primary = ""
    primary = primary.strip() if isinstance(primary, str) else ""
    if primary:
        challenge["primary_technique"] = primary
        challenge["techniques"] = [primary]
    challenge.pop("secondary_technique", None)


def _trim_intended_path(challenge: dict[str, Any], max_steps: int) -> None:
    raw = challenge.get("intended_path")
    if not isinstance(raw, list):
        return
    steps = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    if len(steps) <= max_steps:
        challenge["intended_path"] = steps
        return
    if max_steps <= 1:
        challenge["intended_path"] = steps[:max_steps]
        return
    kept = steps[: max_steps - 1]
    final = steps[-1]
    if final not in kept:
        kept.append(final)
    challenge["intended_path"] = kept[:max_steps]
