"""Schema constants, dataclasses, and exceptions for challenge design validation.

Phase 3 split this out of the 896-line ``domain.challenge_design_validators``
module so that the schema lives in one place and the parser / validator /
quality gate can import only what they need.

The schema is intentionally **flat** — there is no SKILL.md → flat translation
layer. The agent is required to emit the field shape declared here directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------- Defaults ----------

DEFAULT_FLAG_FORMAT = "flag{...}"

# ---------- Length limits ----------

MAX_SUMMARY_CHARS = 280
MAX_IMPLEMENTATION_PLAN_CHARS = 4000
MAX_PLAN_STRING_CHARS = 500

# ---------- Artifact contracts ----------

COMMON_ARTIFACTS: tuple[str, ...] = (
    "README.md",
    "metadata.json",
    "validate.sh",
    "writenup/wp.md",
    "writenup/exp.py",
)

CONTAINER_ARTIFACTS: tuple[str, ...] = (
    "deploy/Dockerfile",
    "deploy/docker-compose.yml",
    "deploy/src/app.py",
    "deploy/_files/start.sh",
)

KNOWN_ARTIFACT_PREFIXES: tuple[str, ...] = (
    "deploy/",
    "writenup/",
    "attachments/",
    "dist/",
    "src/",
)

# ---------- Implementation-leakage guards ----------

# Top-level keys the design agent must NOT emit — they are build-phase
# artifacts. Detecting any of them in challenges[0] is a hard reject.
FORBIDDEN_IMPLEMENTATION_KEYS: frozenset[str] = frozenset(
    {
        "app_code",
        "compose_spec",
        "docker_compose",
        "dockerfile",
        "dockerfile_snippet",
        "exploit_code",
        "exploit_sketch",
        "files_content",
        "init_sql",
        "readme_body",
        "source_code",
        "writeup_body",
    }
)

# Substring markers that indicate the agent is smuggling code inside
# ``implementation_plan`` strings. Match is case-sensitive on purpose.
PLAN_CODE_MARKERS: tuple[str, ...] = (
    "```",
    "#!/bin/bash",
    "<?php",
    "CREATE TABLE",
    "FROM ",
    "RUN apt-get",
    "import requests",
    "services:",
)

# ---------- Required text fields (flat schema) ----------

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

# ---------- URL detection ----------

URL_RE = re.compile(r"https?://", re.IGNORECASE)
HTTP_URL_RE = re.compile(r"https?://[^\s\"'<>`)\]}]+", re.IGNORECASE)

# Loopback hosts the validator allows inside the ``validation`` string.
LOCAL_HTTP_HOSTS: frozenset[str] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "host.docker.internal",
    }
)


# ---------- Exceptions ----------


class ChallengeDesignValidationError(ValueError):
    """Raised when the design agent's JSON output violates the schema."""


# ---------- Validated payload ----------


@dataclass(frozen=True)
class ValidatedDesignPayload:
    """A normalized, validated design ready for persistence.

    ``payload`` is the entire ``{event, challenges}`` object as returned to
    callers; ``challenge`` is the single challenge entry inside it.
    """

    payload: dict[str, Any]
    challenge: dict[str, Any]
    summary: str
    flag_format: str
    validation_notes: str
