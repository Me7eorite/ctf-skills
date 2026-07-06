"""Generation profiles describe delivery/runtime capabilities, not exploits."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.jsonio import read_json
from core.paths import ProjectPaths


DEFAULT_PROFILE_CODES = ("web", "pwn", "re")


@dataclass(frozen=True)
class ChallengeCapability:
    requires_container: bool = False
    requires_network_service: bool = False
    requires_solver: bool = True
    requires_player_artifact: bool = False
    launcher: str | None = None


@dataclass(frozen=True)
class GenerationProfile:
    code: str
    display_name: str
    capabilities: ChallengeCapability
    raw: Mapping[str, Any]


_DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "web": {
        "display_name": "Web",
        "capabilities": {
            "requires_container": True,
            "requires_network_service": True,
            "requires_solver": True,
            "requires_player_artifact": False,
            "launcher": "docker_compose",
        },
    },
    "pwn": {
        "display_name": "Pwn",
        "capabilities": {
            "requires_container": True,
            "requires_network_service": True,
            "requires_solver": True,
            "requires_player_artifact": True,
            "launcher": "xinetd_chroot",
        },
    },
    "re": {
        "display_name": "Reverse",
        "capabilities": {
            "requires_container": False,
            "requires_network_service": False,
            "requires_solver": True,
            "requires_player_artifact": True,
            "launcher": None,
        },
    },
}


def generation_profile(
    category: str | None,
    *,
    paths: ProjectPaths | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> GenerationProfile:
    """Return the declared/default runtime profile for a challenge category."""
    code = str(category or "generic").strip() or "generic"
    payload = _profile_payload(paths or ProjectPaths.discover())
    raw = _raw_profile(payload, code)
    if not raw:
        raw = _DEFAULT_PROFILES.get(code, {})

    capabilities_raw = _capability_mapping(raw)
    capabilities = _capabilities_from_mapping(capabilities_raw)
    if metadata:
        capabilities = _capabilities_from_mapping(
            _metadata_capability_overrides(metadata),
            default=capabilities,
        )

    return GenerationProfile(
        code=code,
        display_name=str(raw.get("display_name") or raw.get("name") or code),
        capabilities=capabilities,
        raw=raw,
    )


def category_profile_config(category: str, *, paths: ProjectPaths | None = None) -> Mapping[str, Any]:
    """Compatibility helper for legacy generation-profiles category settings."""
    return generation_profile(category, paths=paths).raw


def _profile_payload(paths: ProjectPaths) -> Mapping[str, Any]:
    payload = read_json(paths.generation_profile, {})
    return payload if isinstance(payload, Mapping) else {}


def _raw_profile(payload: Mapping[str, Any], code: str) -> Mapping[str, Any]:
    profiles = payload.get("profiles")
    if isinstance(profiles, Mapping):
        row = profiles.get(code)
        if isinstance(row, Mapping):
            return row
    categories = payload.get("categories")
    if isinstance(categories, Mapping):
        row = categories.get(code)
        if isinstance(row, Mapping):
            return row
    return {}


def _capability_mapping(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, Mapping):
        return capabilities
    legacy_keys = {
        key: raw[key]
        for key in (
            "requires_container",
            "requires_network_service",
            "requires_solver",
            "requires_player_artifact",
            "launcher",
        )
        if key in raw
    }
    return legacy_keys


def _metadata_capability_overrides(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = metadata.get("capabilities")
    values: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}
    for key in (
        "requires_container",
        "requires_network_service",
        "requires_solver",
        "requires_player_artifact",
        "launcher",
    ):
        if key in metadata:
            values[key] = metadata[key]
    if "runtime_profile" in metadata and "launcher" not in values:
        runtime_profile = str(metadata.get("runtime_profile") or "").strip()
        if runtime_profile:
            values["launcher"] = runtime_profile
    return values


def _capabilities_from_mapping(
    raw: Mapping[str, Any],
    *,
    default: ChallengeCapability | None = None,
) -> ChallengeCapability:
    base = default or ChallengeCapability()
    return ChallengeCapability(
        requires_container=_bool(raw.get("requires_container"), base.requires_container),
        requires_network_service=_bool(
            raw.get("requires_network_service"), base.requires_network_service
        ),
        requires_solver=_bool(raw.get("requires_solver"), base.requires_solver),
        requires_player_artifact=_bool(
            raw.get("requires_player_artifact"), base.requires_player_artifact
        ),
        launcher=_optional_str(raw.get("launcher"), base.launcher),
    )


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _optional_str(value: Any, default: str | None) -> str | None:
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or default
    return default
