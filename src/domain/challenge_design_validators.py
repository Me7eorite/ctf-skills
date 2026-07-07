"""Backwards-compatible facade for the design validation modules.

Phase 3 split the original 896-line module into focused submodules under
:mod:`domain.design` and removed the SKILL.md → flat translation layer.
New code should import from :mod:`domain.design` directly; this facade
exists so existing imports of ``domain.challenge_design_validators`` keep
working for one release.
"""

from domain.design import (
    DEFAULT_FLAG_FORMAT,
    ChallengeDesignValidationError,
    ValidatedDesignPayload,
    normalize_design_payload_for_task,
    parse_design_output,
    run_quality_gate,
    validate_design_payload,
)

__all__ = [
    "ChallengeDesignValidationError",
    "DEFAULT_FLAG_FORMAT",
    "ValidatedDesignPayload",
    "normalize_design_payload_for_task",
    "parse_design_output",
    "run_quality_gate",
    "validate_design_payload",
]
