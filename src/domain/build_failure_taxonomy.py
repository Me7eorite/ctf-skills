"""Build runner failure classification helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal

BuildFailureCategory = Literal[
    "preflight_workspace",
    "terminal_workspace",
    "materialize",
    "contract_prepare",
    "hermes_auth",
    "hermes_rate_limit",
    "hermes_runtime",
    "hermes_timeout",
    "hermes_cancelled",
    "validation",
]
HERMES_TIMEOUT_RETURNCODE = 124

_FAIL_FAST_ENV = "BUILD_HERMES_FAIL_FAST_MIN_SECONDS"
_DEFAULT_FAIL_FAST_SECONDS = 30


def classify_hermes_exit(
    returncode: int,
    log_tail: str,
    elapsed_seconds: float,
    error_marker: Mapping[str, Any] | None = None,
) -> BuildFailureCategory:
    """Classify a failed Hermes invocation into a stable runner phase."""
    if returncode == 0:
        raise ValueError("classify_hermes_exit expects a non-zero returncode")
    if returncode < 0:
        return "hermes_cancelled"
    if returncode == HERMES_TIMEOUT_RETURNCODE:
        return "hermes_timeout"

    marker = _flatten_marker(error_marker)
    if _marker_is_auth(marker):
        return "hermes_auth"
    if _marker_is_rate_limit(marker):
        return "hermes_rate_limit"

    tail = "" if log_tail is None else str(log_tail)
    if (
        returncode == 1
        and elapsed_seconds < _fail_fast_min_seconds()
        and _tail_is_auth(tail)
    ):
        return "hermes_auth"
    if _tail_is_rate_limit(tail):
        return "hermes_rate_limit"
    return "hermes_runtime"


def _fail_fast_min_seconds() -> int:
    raw = os.environ.get(_FAIL_FAST_ENV)
    if raw is None or raw == "":
        return _DEFAULT_FAIL_FAST_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{_FAIL_FAST_ENV} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{_FAIL_FAST_ENV} must be a positive integer")
    return value


def _flatten_marker(marker: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(marker, Mapping):
        return {}
    values: dict[str, str] = {}
    for key in ("type", "error_type", "code", "status_code", "source"):
        value = marker.get(key)
        if value is not None:
            values[key] = str(value).lower()
    nested = marker.get("error")
    if isinstance(nested, Mapping):
        for key in ("type", "error_type", "code", "status_code"):
            value = nested.get(key)
            if value is not None:
                values.setdefault(key, str(value).lower())
    return values


def _marker_is_auth(marker: Mapping[str, str]) -> bool:
    status = marker.get("status_code")
    if status == "401":
        return True
    combined = " ".join(
        marker.get(key, "") for key in ("type", "error_type", "code")
    )
    return (
        "authentication" in combined
        or "unauthorized" in combined
        or "api_key" in combined
        or "api key" in combined
    )


def _marker_is_rate_limit(marker: Mapping[str, str]) -> bool:
    status = marker.get("status_code")
    if status == "429":
        return True
    combined = " ".join(
        marker.get(key, "") for key in ("type", "error_type", "code")
    )
    return "rate_limit" in combined or "rate limit" in combined or "overloaded" in combined


def _tail_is_auth(log_tail: str) -> bool:
    lower = log_tail.lower()
    if "gic密钥" in log_tail:
        return True
    auth_needles = (
        "anthropic 401",
        "authentication_error",
        "authentication failed",
        "unauthorized",
        "api key",
        "api_key",
        "invalid api",
        "invalid x-api-key",
        "invalid token",
        "token prefix",
        "key is invalid",
        "key has expired",
        "密钥已失效",
    )
    return any(needle in lower for needle in auth_needles)


def _tail_is_rate_limit(log_tail: str) -> bool:
    lower = log_tail.lower()
    provider_context = (
        "rate_limit",
        "rate limit",
        "overloaded_error",
        "overloaded",
        "too many requests",
    )
    if any(needle in lower for needle in provider_context):
        return True
    return "429" in lower and any(
        needle in lower for needle in ("anthropic", "gateway", "provider", "api")
    )
