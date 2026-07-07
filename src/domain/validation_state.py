"""Shared validation status helpers."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Mapping

VALIDATION_FAILURE_FIELDS: tuple[str, ...] = (
    "validation_error",
    "validation_contract_errors",
    "validation_failure_details",
    "validation_failure_class",
    "validation_failure_signature",
    "pwn_failure_stage",
    "pwn_debug_failure_stage",
    "pwn_debug_actionable_summary",
    "pwn_debug_error",
    "repair_result",
    "blocked_reason",
    "expected_next_action",
)


def clear_validation_failure_fields(target: MutableMapping[str, Any]) -> None:
    """Remove stale failure-only validation fields from a successful record."""
    for field in VALIDATION_FAILURE_FIELDS:
        target.pop(field, None)


def authoritative_validation_pass(result: Mapping[str, Any]) -> bool:
    """Return True only for host validation evidence that can become publishable."""
    return (
        result.get("solve_status") == "passed"
        and result.get("validation_status") == "passed"
        and bool(result.get("validation_command"))
        and result.get("validation_returncode") == 0
        and bool(result.get("validation_final_flag_candidate"))
        and result.get("missing_solver_output") is not True
        and not result.get("validation_contract_errors")
    )


def governed_observation_pass(result: Mapping[str, Any]) -> bool:
    """Return True when a governed result carries accepted observation evidence."""

    if result.get("governed") is not True:
        return True
    return result.get("observation_effectively_accepted") is True
