"""Extract the design JSON object from Hermes stdout.

Phase 3 split this parser out of the monolithic validator. The model is
asked (via Output Contract) to reply with a single JSON object whose first
character is ``{`` and last character is ``}``; in practice it occasionally
wraps the payload in markdown fences, prepends a flag string containing
``{...}``, or writes a summary line first. The scanner below tolerates all
three by scanning every ``{`` until it finds an object with both top-level
keys ``event`` and ``challenges``.
"""

from __future__ import annotations

import json
from typing import Any

from domain.design.schema import ChallengeDesignValidationError


def parse_design_output(stdout: str) -> dict[str, Any]:
    """Return the first ``{event, challenges}`` JSON object found in ``stdout``.

    Raises :class:`ChallengeDesignValidationError` with a diagnostic that
    names the Output Contract when nothing matches, so operators don't have
    to guess whether the agent wrote to a file or replied with prose.
    """
    if not isinstance(stdout, str) or not stdout.strip():
        raise ChallengeDesignValidationError("Hermes output is empty")

    text = _strip_json_fences(stdout)
    saw_any_brace = False
    last_decode_error: str | None = None

    cursor = 0
    while True:
        start = text.find("{", cursor)
        if start < 0:
            break
        saw_any_brace = True

        end = _find_balanced_json_object_end(text, start)
        if end is None:
            # Braces are unbalanced from here on — no point scanning further.
            break

        block = text[start : end + 1]
        cursor = end + 1

        try:
            parsed = json.loads(block)
        except json.JSONDecodeError as exc:
            last_decode_error = exc.msg
            continue

        if isinstance(parsed, dict) and "event" in parsed and "challenges" in parsed:
            return parsed
        # Parsed but not a design payload — keep scanning.

    if not saw_any_brace:
        raise ChallengeDesignValidationError("Hermes output does not contain JSON")
    if last_decode_error is not None:
        raise ChallengeDesignValidationError(
            "Hermes output does not contain a JSON object with `event` and "
            f"`challenges` (last decode error: {last_decode_error})"
        )
    raise ChallengeDesignValidationError(
        "Hermes output does not contain a JSON object with `event` and "
        "`challenges`; the agent likely wrote the design to a file or replied "
        "with prose. The Output Contract requires the reply itself to be the "
        "JSON object."
    )


def _strip_json_fences(text: str) -> str:
    """Strip a single surrounding ```` ``` ```` or ```` ```json ```` fence."""
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
    """Return the index of the ``}`` that closes the object starting at ``start``.

    Tracks string literals and escapes so that braces inside JSON strings do
    not affect nesting depth. Returns ``None`` when no balanced end exists.
    """
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
