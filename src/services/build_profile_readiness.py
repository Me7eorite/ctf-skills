"""Build-profile readiness checks shared by startup and HTTP dispatch."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from hermes.process import profile_exists

BUILD_PROFILES = {
    "web": "cf-web",
    "pwn": "cf-pwn",
    "re": "cf-re",
}


def check_build_profile_readiness(
    exists: Callable[[str], bool] = profile_exists,
) -> dict[str, Any]:
    categories = {}
    for category, profile_name in BUILD_PROFILES.items():
        ready = exists(profile_name)
        categories[category] = {
            "ready": ready,
            "profile": profile_name,
            "create_command": f"hermes profile create {profile_name}",
        }
    missing = [
        item["profile"] for item in categories.values() if not item["ready"]
    ]
    return {
        "ready": not missing,
        "categories": categories,
        "missing_profiles": missing,
    }


def unavailable_build_profiles(
    readiness: dict[str, Any],
    categories: Iterable[str],
) -> list[dict[str, str]]:
    states = readiness.get("categories", {})
    unavailable = []
    for category in dict.fromkeys(categories):
        state = states.get(category, {})
        if state.get("ready"):
            continue
        profile_name = state.get("profile") or BUILD_PROFILES.get(
            category, f"cf-{category}"
        )
        unavailable.append(
            {
                "category": category,
                "profile": profile_name,
                "create_command": state.get("create_command")
                or f"hermes profile create {profile_name}",
            }
        )
    return unavailable
