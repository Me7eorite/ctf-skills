"""Build hard-timeout policy shared by runner, CLI dispatch, and Web UI."""

from __future__ import annotations

from typing import Any, Mapping

from core.queue import SUPPORTED_CATEGORIES

VALIDATION_REPAIR_TIMEOUT_CAP = 600


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
