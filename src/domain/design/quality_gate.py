"""Deterministic quality-gate checks for a validated design payload.

The quality gate runs after :func:`domain.design.validator.validate_design_payload`
and reports human-readable notes about anything that looks weak even though
the structural validator passed. Callers persist both the pass/fail flag
and the notes alongside the design row.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from domain.design.schema import ChallengeDesignValidationError
from domain.design.validator import _is_absolute_or_url_path
from domain.research import DIFFICULTY_LABELS


def run_quality_gate(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Return ``(passed, notes)`` for ``payload`` (single-challenge shape)."""
    notes: list[str] = []
    try:
        challenge = _single_challenge(payload)
    except ChallengeDesignValidationError as exc:
        return False, [str(exc)]

    _note_if(
        notes,
        not isinstance(challenge.get("learning_objective"), str)
        or not challenge["learning_objective"].strip(),
        "learning objective is missing",
    )
    _note_if(
        notes,
        not isinstance(challenge.get("validation"), str)
        or not challenge["validation"].strip(),
        "validation plan is missing",
    )
    _note_if(
        notes,
        not isinstance(challenge.get("hints"), list)
        or len(challenge["hints"]) != 3,
        "hints are not staged as three entries",
    )
    _note_if(
        notes,
        challenge.get("difficulty") not in DIFFICULTY_LABELS,
        "difficulty is not canonical",
    )

    artifacts = challenge.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        notes.append("artifacts are missing")
    else:
        for artifact in artifacts:
            if not isinstance(artifact, str) or _is_absolute_or_url_path(artifact):
                notes.append("artifacts must be relative paths")
                break

    category = challenge.get("category")
    if category in {"web", "pwn"}:
        deployment = challenge.get("deployment")
        _note_if(
            notes,
            not isinstance(deployment, str)
            or "docker" not in deployment.lower(),
            "web/pwn deployment must be containerized",
        )
        _note_if(
            notes, "port" not in challenge, "web/pwn design must define a port"
        )

    return not notes, notes


def _single_challenge(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    challenges = payload.get("challenges")
    if not isinstance(challenges, list) or len(challenges) != 1:
        raise ChallengeDesignValidationError(
            "challenges must contain exactly one entry"
        )
    challenge = challenges[0]
    if not isinstance(challenge, Mapping):
        raise ChallengeDesignValidationError("challenge entry must be an object")
    return challenge


def _note_if(notes: list[str], condition: bool, note: str) -> None:
    if condition:
        notes.append(note)
