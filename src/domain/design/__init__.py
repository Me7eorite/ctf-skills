"""Structured challenge-design validation, split by responsibility.

Phase 3 split the original ``domain.challenge_design_validators`` (896 lines)
into focused modules and removed the SKILL.md → flat translation layer.
The legacy module path is kept as a thin facade so call sites do not
have to change.

Public surface:

- :class:`ChallengeDesignValidationError`
- :data:`DEFAULT_FLAG_FORMAT`
- :class:`ValidatedDesignPayload`
- :func:`parse_design_output`
- :func:`validate_design_payload`
- :func:`run_quality_gate`
"""

from domain.design.difficulty import RUBRIC, validate_difficulty_alignment
from domain.design.parser import parse_design_output
from domain.design.quality_gate import run_quality_gate
from domain.design.schema import (
    DEFAULT_FLAG_FORMAT,
    ChallengeDesignValidationError,
    ValidatedDesignPayload,
)
from domain.design.validator import validate_design_payload

__all__ = [
    "ChallengeDesignValidationError",
    "DEFAULT_FLAG_FORMAT",
    "RUBRIC",
    "ValidatedDesignPayload",
    "parse_design_output",
    "run_quality_gate",
    "validate_design_payload",
    "validate_difficulty_alignment",
]
