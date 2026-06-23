"""Structural validation for a single challenge design payload.

Phase 3 cut the SKILL.md → flat-shape normalizer that used to live here.
The schema is now declared once in :mod:`domain.design.schema` and the
agent is required to emit it directly. Common rejections:

- ``player_prompt`` / nested ``validation`` / ``flag_plan.location`` are no
  longer translated; use ``prompt`` / ``validation`` / ``flag_location``.
- ``artifacts`` and ``hints`` must be flat lists. Mappings, prose, and
  legacy paths (``writeup/wp.md``, ``solve.py``) are rejected.
- Required artifact entries (``COMMON_ARTIFACTS`` plus ``CONTAINER_ARTIFACTS``
  for ``web``/``pwn``) MUST be listed by the agent; the validator no longer
  auto-fills them.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from domain.design.difficulty import validate_difficulty_alignment
from domain.design.schema import (
    COMMON_ARTIFACTS,
    CONTAINER_ARTIFACTS,
    DEFAULT_FLAG_FORMAT,
    FORBIDDEN_IMPLEMENTATION_KEYS,
    HTTP_URL_RE,
    KNOWN_ARTIFACT_PREFIXES,
    LOCAL_HTTP_HOSTS,
    MAX_IMPLEMENTATION_PLAN_CHARS,
    MAX_PLAN_STRING_CHARS,
    MAX_SUMMARY_CHARS,
    PLAN_CODE_MARKERS,
    REQUIRED_CHALLENGE_TEXT_FIELDS,
    URL_RE,
    ChallengeDesignValidationError,
    ValidatedDesignPayload,
)
from domain.design_tasks import DesignTask
from domain.research import DIFFICULTY_LABELS


def validate_design_payload(
    payload: Mapping[str, Any],
    parent_task: DesignTask,
    *,
    legacy_grandfather: bool = False,
) -> ValidatedDesignPayload:
    """Validate a single ``{event, challenges}`` payload against ``parent_task``.

    Returns a :class:`ValidatedDesignPayload` carrying the normalized
    payload, the single challenge entry, a generated summary, the
    canonical ``flag_format``, and the ``validation`` string.

    ``legacy_grandfather`` is forwarded to the difficulty-alignment check
    so designs created before the rubric existed can be re-validated
    without forcing a rewrite.
    """
    if not isinstance(payload, Mapping):
        raise ChallengeDesignValidationError("design payload must be an object")

    normalized = copy.deepcopy(dict(payload))

    # ---- event ----
    event = normalized.get("event")
    if not isinstance(event, dict):
        raise ChallengeDesignValidationError("event must be an object")
    flag_format = event.get("flag_format")
    if flag_format is None:
        event["flag_format"] = DEFAULT_FLAG_FORMAT
        flag_format = DEFAULT_FLAG_FORMAT
    if not isinstance(flag_format, str) or not flag_format.strip():
        raise ChallengeDesignValidationError(
            "event.flag_format must be a non-empty string"
        )

    # ---- challenges ----
    challenges = normalized.get("challenges")
    if not isinstance(challenges, list) or len(challenges) != 1:
        raise ChallengeDesignValidationError(
            "challenges must be an array of length 1"
        )
    challenge = challenges[0]
    if not isinstance(challenge, dict):
        raise ChallengeDesignValidationError("challenges[0] must be an object")

    _reject_implementation_payload(challenge)
    _validate_implementation_plan(challenge.get("implementation_plan"))

    for field in REQUIRED_CHALLENGE_TEXT_FIELDS:
        _require_non_empty_string(challenge, field)

    _require_parent_equal(challenge, "id", parent_task.challenge_id)
    _require_parent_equal(challenge, "category", parent_task.category)
    _require_parent_equal(challenge, "difficulty", parent_task.difficulty)

    points = challenge.get("points")
    if not isinstance(points, int) or isinstance(points, bool) or points <= 0:
        raise ChallengeDesignValidationError("points must be a positive integer")
    if points != parent_task.points:
        raise ChallengeDesignValidationError(
            "points must equal parent design task points"
        )

    if challenge["difficulty"] not in DIFFICULTY_LABELS:
        raise ChallengeDesignValidationError("difficulty is not canonical")

    _validate_artifacts(challenge)
    _validate_hints(challenge)

    validation = challenge["validation"]
    if not isinstance(validation, str):
        raise ChallengeDesignValidationError("validation must be a string")
    if _contains_external_http_url(validation):
        raise ChallengeDesignValidationError(
            "validation must not contain external HTTP URLs"
        )

    if parent_task.category in {"web", "pwn"}:
        deployment = challenge["deployment"].lower()
        if "docker" not in deployment:
            raise ChallengeDesignValidationError(
                "web/pwn deployment must mention docker"
            )
        if challenge.get("port") != parent_task.port:
            raise ChallengeDesignValidationError(
                "port must equal parent design task port"
            )
        _require_artifacts(
            challenge["artifacts"],
            (*COMMON_ARTIFACTS, *CONTAINER_ARTIFACTS),
            "web/pwn artifacts",
        )
    else:
        _require_artifacts(challenge["artifacts"], COMMON_ARTIFACTS, "artifacts")

    # Difficulty-aware content alignment runs last so structural errors
    # take precedence in the operator-facing message.
    validate_difficulty_alignment(
        challenge, parent_task, legacy_grandfather=legacy_grandfather
    )

    summary = _make_summary(challenge)
    return ValidatedDesignPayload(
        payload=normalized,
        challenge=challenge,
        summary=summary,
        flag_format=flag_format.strip(),
        validation_notes=validation.strip(),
    )


# ---------- Field-shape helpers ----------


def _validate_artifacts(challenge: dict[str, Any]) -> None:
    """Require ``artifacts`` to be a non-empty flat list of relative paths."""
    artifacts = challenge.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ChallengeDesignValidationError(
            "artifacts must be a non-empty list of relative file paths"
        )
    for entry in artifacts:
        if not isinstance(entry, str) or not entry.strip():
            raise ChallengeDesignValidationError(
                "artifacts must contain non-empty strings"
            )
        if _is_absolute_or_url_path(entry):
            raise ChallengeDesignValidationError(
                "artifacts must be relative paths"
            )
        if not _is_artifact_path_like(entry):
            raise ChallengeDesignValidationError(
                "artifacts must be local challenge-relative file paths; "
                f"invalid entry: {entry!r}. Use README.md, metadata.json, "
                "validate.sh, or a path under deploy/, writenup/, "
                "attachments/, or src/"
            )


def _validate_hints(challenge: dict[str, Any]) -> None:
    """Require exactly 3 non-empty string hints."""
    hints = challenge.get("hints")
    if not isinstance(hints, list) or len(hints) != 3:
        raise ChallengeDesignValidationError(
            "hints must contain exactly 3 entries"
        )
    for hint in hints:
        if not isinstance(hint, str) or not hint.strip():
            raise ChallengeDesignValidationError(
                "hints must contain non-empty strings"
            )


def _require_artifacts(
    artifacts: list[str], required: tuple[str, ...], label: str
) -> None:
    missing = [path for path in required if path not in artifacts]
    if missing:
        raise ChallengeDesignValidationError(
            f"{label} must include: {', '.join(missing)}"
        )


def _reject_implementation_payload(challenge: Mapping[str, Any]) -> None:
    present = sorted(key for key in FORBIDDEN_IMPLEMENTATION_KEYS if key in challenge)
    if present:
        raise ChallengeDesignValidationError(
            "design output includes implementation-level fields: "
            + ", ".join(present)
        )


# ---------- implementation_plan recursion ----------


def _validate_implementation_plan(plan: Any) -> None:
    if plan is None:
        return
    if not isinstance(plan, Mapping):
        raise ChallengeDesignValidationError(
            "implementation_plan must be an object"
        )

    components = plan.get("components")
    if components is not None:
        if not isinstance(components, list):
            raise ChallengeDesignValidationError(
                "implementation_plan.components must be an array of component names"
            )
        if any(
            not isinstance(component, str) or not component.strip()
            for component in components
        ):
            raise ChallengeDesignValidationError(
                "implementation_plan.components must contain non-empty strings"
            )

    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    if len(encoded) > MAX_IMPLEMENTATION_PLAN_CHARS:
        raise ChallengeDesignValidationError(
            "implementation_plan is too large; keep it intent-level"
        )

    _validate_plan_value(plan, path="implementation_plan")


def _validate_plan_value(value: Any, *, path: str) -> None:
    if isinstance(value, str):
        if len(value) > MAX_PLAN_STRING_CHARS:
            raise ChallengeDesignValidationError(
                f"{path} contains a string longer than {MAX_PLAN_STRING_CHARS} characters"
            )
        if any(marker in value for marker in PLAN_CODE_MARKERS):
            raise ChallengeDesignValidationError(
                "implementation_plan must be intent-level, not file contents"
            )
        return

    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in FORBIDDEN_IMPLEMENTATION_KEYS:
                raise ChallengeDesignValidationError(
                    "implementation_plan contains implementation-level field: "
                    f"{key}"
                )
            _validate_plan_value(item, path=f"{path}.{key}")
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_plan_value(item, path=f"{path}[{index}]")


# ---------- Small helpers ----------


def _require_non_empty_string(challenge: Mapping[str, Any], field: str) -> None:
    value = challenge.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ChallengeDesignValidationError(
            f"{field} must be a non-empty string"
        )


def _require_parent_equal(
    challenge: Mapping[str, Any], field: str, expected: Any
) -> None:
    if challenge.get(field) != expected:
        raise ChallengeDesignValidationError(
            f"{field} must equal parent design task value"
        )


def _is_absolute_or_url_path(value: str) -> bool:
    stripped = value.strip()
    return (
        bool(URL_RE.search(stripped))
        or stripped.startswith("/")
        or stripped.startswith("\\")
        or bool(re.match(r"^[A-Za-z]:[\\/]", stripped))
    )


def _contains_external_http_url(value: str) -> bool:
    for match in HTTP_URL_RE.finditer(value):
        raw_url = match.group(0).rstrip(".,;:")
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            return True
        host = (parsed.hostname or "").lower()
        if host not in LOCAL_HTTP_HOSTS and not host.startswith("127."):
            return True
    return False


def _is_artifact_path_like(value: str) -> bool:
    stripped = value.strip().replace("\\", "/")
    if not stripped or _is_absolute_or_url_path(stripped):
        return False
    if any(char in stripped for char in "\r\n\t"):
        return False
    parts = stripped.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    if stripped in COMMON_ARTIFACTS or stripped in CONTAINER_ARTIFACTS:
        return True
    if stripped.startswith(KNOWN_ARTIFACT_PREFIXES):
        # Native binaries and conventional build files commonly have no
        # suffix (for example ``attachments/crackme`` and
        # ``deploy/Makefile``).  Their containing challenge-local directory,
        # not a filename extension, is the security boundary.
        return bool(PurePosixPath(stripped).name)
    return stripped in {"README.md", "metadata.json", "validate.sh"}


def _make_summary(challenge: Mapping[str, Any]) -> str:
    title = str(challenge.get("title", "")).strip()
    technique = str(challenge.get("primary_technique", "")).strip()
    objective = str(challenge.get("learning_objective", "")).strip()
    parts = [part for part in (title, technique, objective) if part]
    summary = " - ".join(parts) or "Structured challenge design"
    return summary[:MAX_SUMMARY_CHARS]
