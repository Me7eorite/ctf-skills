"""Validation helpers for structured challenge-design output."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from domain.design_tasks import DesignTask
from domain.research import DIFFICULTY_LABELS

DEFAULT_FLAG_FORMAT = "flag{...}"
MAX_SUMMARY_CHARS = 280

REQUIRED_CHALLENGE_TEXT_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "category",
    "difficulty",
    "deployment",
    "primary_technique",
    "learning_objective",
    "prompt",
    "flag_location",
    "validation",
)

URL_RE = re.compile(r"https?://", re.IGNORECASE)


class ChallengeDesignValidationError(ValueError):
    """Raised when design-agent JSON output is invalid."""


@dataclass(frozen=True)
class ValidatedDesignPayload:
    payload: dict[str, Any]
    challenge: dict[str, Any]
    summary: str
    flag_format: str
    validation_notes: str


def parse_design_output(stdout: str) -> dict[str, Any]:
    """Extract the first balanced JSON object from Hermes stdout."""
    if not isinstance(stdout, str) or not stdout.strip():
        raise ChallengeDesignValidationError("Hermes output is empty")

    text = _strip_json_fences(stdout)
    start = text.find("{")
    if start < 0:
        raise ChallengeDesignValidationError("Hermes output does not contain JSON")

    end = _find_balanced_json_object_end(text, start)
    if end is None:
        raise ChallengeDesignValidationError("Hermes output contains unbalanced JSON")

    block = text[start : end + 1]
    try:
        parsed = json.loads(block)
    except json.JSONDecodeError as exc:
        raise ChallengeDesignValidationError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ChallengeDesignValidationError("design output JSON must be an object")
    return parsed


def validate_design_payload(
    payload: Mapping[str, Any],
    parent_task: DesignTask,
) -> ValidatedDesignPayload:
    """Validate and normalize one design-challenges JSON payload."""
    if not isinstance(payload, Mapping):
        raise ChallengeDesignValidationError("design payload must be an object")

    normalized = copy.deepcopy(dict(payload))
    event = normalized.get("event")
    if not isinstance(event, dict):
        raise ChallengeDesignValidationError("event must be an object")
    flag_format = event.get("flag_format")
    if flag_format is None:
        event["flag_format"] = DEFAULT_FLAG_FORMAT
        flag_format = DEFAULT_FLAG_FORMAT
    if not isinstance(flag_format, str) or not flag_format.strip():
        raise ChallengeDesignValidationError("event.flag_format must be a non-empty string")

    challenges = normalized.get("challenges")
    if not isinstance(challenges, list) or len(challenges) != 1:
        raise ChallengeDesignValidationError("challenges must be an array of length 1")
    challenge = challenges[0]
    if not isinstance(challenge, dict):
        raise ChallengeDesignValidationError("challenges[0] must be an object")

    for field in REQUIRED_CHALLENGE_TEXT_FIELDS:
        _require_non_empty_string(challenge, field)

    _require_parent_equal(challenge, "id", parent_task.challenge_id)
    _require_parent_equal(challenge, "category", parent_task.category)
    _require_parent_equal(challenge, "difficulty", parent_task.difficulty)

    points = challenge.get("points")
    if not isinstance(points, int) or isinstance(points, bool) or points <= 0:
        raise ChallengeDesignValidationError("points must be a positive integer")
    if points != parent_task.points:
        raise ChallengeDesignValidationError("points must equal parent design task points")

    if challenge["difficulty"] not in DIFFICULTY_LABELS:
        raise ChallengeDesignValidationError("difficulty is not canonical")

    artifacts = challenge.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ChallengeDesignValidationError("artifacts must be a non-empty array")
    for artifact in artifacts:
        if not isinstance(artifact, str) or not artifact.strip():
            raise ChallengeDesignValidationError("artifacts must contain non-empty strings")
        if _is_absolute_or_url_path(artifact):
            raise ChallengeDesignValidationError("artifacts must be relative paths")

    hints = challenge.get("hints")
    if not isinstance(hints, list) or len(hints) != 3:
        raise ChallengeDesignValidationError("hints must contain exactly 3 entries")
    for hint in hints:
        if not isinstance(hint, str) or not hint.strip():
            raise ChallengeDesignValidationError("hints must contain non-empty strings")

    validation = challenge["validation"]
    if URL_RE.search(validation):
        raise ChallengeDesignValidationError("validation must not contain HTTP URLs")

    if parent_task.category in {"web", "pwn"}:
        deployment = challenge["deployment"].lower()
        if "docker" not in deployment:
            raise ChallengeDesignValidationError("web/pwn deployment must mention docker")
        port = challenge.get("port")
        if port != parent_task.port:
            raise ChallengeDesignValidationError("port must equal parent design task port")

    summary = _make_summary(challenge)
    return ValidatedDesignPayload(
        payload=normalized,
        challenge=challenge,
        summary=summary,
        flag_format=flag_format.strip(),
        validation_notes=validation.strip(),
    )


def run_quality_gate(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Run explicit deterministic quality predicates derived from quality-gate.md."""
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
        not isinstance(challenge.get("validation"), str) or not challenge["validation"].strip(),
        "validation plan is missing",
    )
    _note_if(
        notes,
        not isinstance(challenge.get("hints"), list) or len(challenge["hints"]) != 3,
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
            not isinstance(deployment, str) or "docker" not in deployment.lower(),
            "web/pwn deployment must be containerized",
        )
        _note_if(notes, "port" not in challenge, "web/pwn design must define a port")

    return not notes, notes


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().lower() in {"```json", "```"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _find_balanced_json_object_end(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _require_non_empty_string(challenge: Mapping[str, Any], field: str) -> None:
    value = challenge.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ChallengeDesignValidationError(f"{field} must be a non-empty string")


def _require_parent_equal(challenge: Mapping[str, Any], field: str, expected: Any) -> None:
    if challenge.get(field) != expected:
        raise ChallengeDesignValidationError(f"{field} must equal parent design task value")


def _is_absolute_or_url_path(value: str) -> bool:
    stripped = value.strip()
    return (
        bool(URL_RE.search(stripped))
        or stripped.startswith("/")
        or stripped.startswith("\\")
        or bool(re.match(r"^[A-Za-z]:[\\/]", stripped))
    )


def _single_challenge(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    challenges = payload.get("challenges")
    if not isinstance(challenges, list) or len(challenges) != 1:
        raise ChallengeDesignValidationError("challenges must contain exactly one entry")
    challenge = challenges[0]
    if not isinstance(challenge, Mapping):
        raise ChallengeDesignValidationError("challenge entry must be an object")
    return challenge


def _make_summary(challenge: Mapping[str, Any]) -> str:
    title = str(challenge.get("title", "")).strip()
    technique = str(challenge.get("primary_technique", "")).strip()
    objective = str(challenge.get("learning_objective", "")).strip()
    parts = [part for part in (title, technique, objective) if part]
    summary = " - ".join(parts) or "Structured challenge design"
    return summary[:MAX_SUMMARY_CHARS]


def _note_if(notes: list[str], condition: bool, note: str) -> None:
    if condition:
        notes.append(note)
