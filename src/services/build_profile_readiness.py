"""Build-profile readiness checks shared by startup and HTTP dispatch."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from core.paths import ProjectPaths
from hermes.process import effective_terminal_backend, profile_exists

BUILD_PROFILES = {
    "web": "cf-web",
    "pwn": "cf-pwn",
    "re": "cf-re",
}
_PWN_LOCAL_BACKEND_MESSAGE = (
    "PWN build profile requires an isolated Docker/VM terminal backend; "
    "local or unknown backends are refused to protect host system tools"
)


def check_build_profile_readiness(
    exists: Callable[[str], bool] = profile_exists,
    *,
    paths: ProjectPaths | None = None,
    terminal_backend: str | None = None,
) -> dict[str, Any]:
    categories = {}
    for category, profile_name in BUILD_PROFILES.items():
        profile_backend = terminal_backend
        if profile_backend is None and paths is not None:
            profile_backend = effective_terminal_backend(
                paths.hermes_home,
                profile_name=profile_name,
            )
        profile_present = exists(profile_name)
        ready = profile_present
        reason = "" if profile_present else "missing_profile"
        message = ""
        if category == "pwn" and (not profile_backend or profile_backend.lower() == "local"):
            ready = False
            if profile_present:
                reason = "unsafe_terminal_backend"
            message = _PWN_LOCAL_BACKEND_MESSAGE
        categories[category] = {
            "ready": ready,
            "profile": profile_name,
            "reason": reason,
            "backend": profile_backend or "",
            "create_command": f"hermes profile create {profile_name}",
            "message": message,
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
                "message": state.get("message", ""),
            }
        )
    return unavailable
