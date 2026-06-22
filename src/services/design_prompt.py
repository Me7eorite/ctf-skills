"""Prompt assembly for structured challenge design attempts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.paths import ProjectPaths
from domain.design.difficulty import RUBRIC as DIFFICULTY_RUBRIC
from domain.design_tasks import DesignTask
from domain.research import GenerationRequest, ResearchFinding, ResearchSource

# Phase 1 (9-references → 3): the design skill is now a single core file
# plus a unified category-tactics catalog. cve-pivot.md is read on-demand by
# the agent, not injected into every design prompt. delivery-format moved to
# docs/delivery-formats/ and is no longer part of design.
# Phase 2 added difficulty-rubric.md so the agent sees the machine-checked
# tier thresholds (technique count, intended_path steps, novelty requirement).
ALWAYS_REFERENCE_FILES: tuple[str, ...] = (
    "design-core.md",
    "category-tactics.md",
    "difficulty-rubric.md",
)
EVIDENCE_FINDING_LIMIT = 20
MAX_REFERENCE_CHARS = 5000


@dataclass(frozen=True)
class DesignPromptContext:
    skill_text: str
    references: Mapping[str, str]


def load_design_prompt_context(paths: ProjectPaths) -> DesignPromptContext:
    """Read the design skill and all reference files used by the prompt."""
    references = {
        name: (paths.design_references / name).read_text(encoding="utf-8")
        for name in sorted(ALWAYS_REFERENCE_FILES)
    }
    return DesignPromptContext(
        skill_text=paths.design_skill.read_text(encoding="utf-8"),
        references=references,
    )


def build_design_prompt(
    context: DesignPromptContext,
    design_task: DesignTask,
    generation_request: GenerationRequest,
    findings: Sequence[ResearchFinding],
    sources: Sequence[ResearchSource],
    previous_error: str | None = None,
) -> str:
    """Build a deterministic Hermes prompt without filesystem or DB access."""
    reference_names = list(ALWAYS_REFERENCE_FILES)

    sections = [
        "# Structured Challenge Design Attempt",
        "## Skill",
        "/skill design-challenges",
        "",
        _render_reference("skills/design-challenges/SKILL.md", context.skill_text),
        "## Event Brief",
        _render_event_brief(generation_request),
        "## Single Challenge Task",
        _render_design_task(design_task),
        "## Build Budget",
        _render_build_budget(design_task.difficulty),
        "## Research Evidence",
        _render_findings(findings),
        "## Research Sources",
        _render_sources(sources),
        "## References",
        *(
            _render_reference(
                f"skills/design-challenges/references/{name}",
                context.references[name],
            )
            for name in reference_names
        ),
        _render_retry_feedback(previous_error),
        "## Output Contract",
        _render_output_contract(design_task),
        "## Pinned Values (copy verbatim into `challenges[0]`)",
        _render_pinned_values(design_task),
    ]
    return "\n\n".join(sections).rstrip() + "\n"


# Phase 4: the Output Contract used to be 25+ negative don't-rules. It
# is now a JSON Schema + 3 short invariants. The validator side
# (``domain.design.validator``) is the authoritative enforcement; the
# schema below is the agent-facing summary that mirrors it so the model
# can self-check before replying.
_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["event", "challenges"],
    "additionalProperties": False,
    "properties": {
        "event": {
            "type": "object",
            "required": ["flag_format"],
            "properties": {
                "name": {"type": "string"},
                "theme": {"type": "string"},
                "audience": {"type": "string"},
                "flag_format": {"type": "string"},
            },
        },
        "challenges": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "id", "title", "category", "difficulty", "points",
                    "deployment", "primary_technique", "learning_objective",
                    "prompt", "flag_location", "validation",
                    "artifacts", "hints", "intended_path",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "difficulty": {
                        "enum": ["easy", "medium", "hard", "expert"]
                    },
                    "points": {"type": "integer", "minimum": 1},
                    "deployment": {"type": "string"},
                    "port": {"type": ["integer", "null"]},
                    "techniques": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "primary_technique": {"type": "string", "minLength": 1},
                    "secondary_technique": {"type": "string"},
                    "learning_objective": {"type": "string", "minLength": 1},
                    "prompt": {"type": "string", "minLength": 1},
                    "flag_location": {"type": "string", "minLength": 1},
                    "flag_plan": {
                        "type": "object",
                        "properties": {
                            "format": {"type": "string"},
                            "location": {"type": "string"},
                            "generation": {"type": "string"},
                        },
                    },
                    "intended_path": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "artifacts": {
                        "type": "array",
                        "minItems": 5,
                        "items": {
                            "type": "string",
                            "description": (
                                "A safe challenge-relative file path. Native "
                                "executables and Makefiles may be extensionless."
                            ),
                            "pattern": (
                                r"^(?:README\.md|metadata\.json|validate\.sh|"
                                r"(?:deploy|writenup|attachments|dist|src)/"
                                r"(?!\.\.(?:/|$))(?!.*\/\.\.(?:/|$))"
                                r"[^\r\n\t/]+(?:/[^\r\n\t/]+)*)$"
                            ),
                        },
                    },
                    "validation": {"type": "string", "minLength": 1},
                    "hints": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "implementation_plan": {
                        "type": "object",
                        "description": (
                            "Intent-level only. NO Dockerfile bodies, NO "
                            "compose YAML, NO SQL scripts, NO exploit code, "
                            "NO file contents. Component cap depends on "
                            "difficulty (see Build Budget section)."
                        ),
                        "properties": {
                            "components": {
                                "type": "array",
                                "description": (
                                    "Optional names of independently buildable "
                                    "or deployable components. Do not list metadata "
                                    "fields such as runtime, entrypoints, or flag handling."
                                ),
                                "items": {"type": "string", "minLength": 1},
                            }
                        },
                    },
                    "novelty": {
                        "type": "string",
                        "description": (
                            "Required for `expert`. ≥ 40 chars. Identifies "
                            "the 0day-style trick or unusual constraint."
                        ),
                    },
                },
            },
        },
    },
}


def _render_output_contract(task: DesignTask) -> str:
    """Render the JSON Schema + 3 short invariants.

    Phase 4: the old 25-line negative-list got modelled into the schema
    above so the agent can self-validate against one block instead of
    scanning a wall of prose rules.
    """
    schema_text = json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    container_artifacts_hint = (
        "\n- For web/pwn, `artifacts` must additionally include "
        "`deploy/Dockerfile`, `deploy/docker-compose.yml`, "
        "`deploy/src/app.py`, and `deploy/_files/start.sh`."
        if task.category in {"web", "pwn"}
        else ""
    )
    invariants = (
        "Invariants (enforced server-side; violating any of these fails "
        "the attempt):\n"
        "1. Your reply MUST be a SINGLE JSON object matching the schema "
        "below — no markdown, code fences, prose, file writes, or "
        "secondary artifacts.\n"
        "2. `artifacts` MUST be relative local paths and MUST include "
        "`README.md`, `metadata.json`, `validate.sh`, `writenup/wp.md`, "
        "and `writenup/exp.py`. Extensionless native executables and "
        "conventional build files are valid; for example "
        "`attachments/crackme`, `dist/crackme`, and `deploy/Makefile`."
        + container_artifacts_hint
        + "\n3. `validation` MAY reference local compose URLs "
        "(`http://127.0.0.1:<port>`, `http://localhost:<port>`) but MUST "
        "NOT require external HTTP/HTTPS URLs, and MUST NOT contain code "
        "or file bodies."
        + "\n4. For `category = re`, do not make the delivered artifact "
        "trivially reveal `metadata.flag` via `strings` unless "
        "`primary_technique` explicitly says the intended solve is "
        "`strings on the binary`; likewise, `validate.sh` and "
        "`writenup/exp.py` MUST NOT embed the literal `metadata.flag`."
    )
    return f"{invariants}\n\n```json\n{schema_text}\n```"


def _render_retry_feedback(previous_error: str | None) -> str:
    """Tell a retry what the preceding attempt must correct."""
    if not previous_error:
        return ""
    concise_error = previous_error.strip()[:1000]
    return "\n".join(
        [
            "## Retry Feedback",
            "The preceding attempt failed server-side validation. Correct this "
            "specific problem before replying:",
            "",
            f"- {concise_error}",
            "- Re-check the complete Output Contract after making the correction.",
        ]
    )


def _render_build_budget(difficulty: str) -> str:
    """Quote the per-tier buildability caps so the agent self-constrains.

    Phase 2.5 (D5=a): timeouts stay category-based (set in core/build_timeout);
    this block keeps the design within the scope the build phase can actually
    finish before hitting them.
    """
    rubric = DIFFICULTY_RUBRIC.get(difficulty)
    if rubric is None:
        return "(unknown difficulty — no budget enforced)"
    return "\n".join(
        [
            f"Buildability budget for `{difficulty}` (enforced by validator + "
            "consumed by the build agent):",
            "",
            f"- techniques: {_range_text(rubric.techniques_min, rubric.techniques_max)}",
            f"- intended_path steps: {_range_text(rubric.intended_path_min, rubric.intended_path_max)}",
            f"- explicit `implementation_plan.components` entries: ≤ "
            f"{rubric.implementation_component_max}",
            f"- estimated total build LOC (guidance, not enforced): ≤ "
            f"{rubric.estimated_loc_budget}",
            f"- business scenario required: "
            f"{'yes' if rubric.needs_business_scenario else 'no'}",
            f"- implementation_plan required: "
            f"{'yes' if rubric.needs_implementation_plan else 'no'}",
            f"- novelty field required: "
            f"{'yes' if rubric.needs_novelty else 'no'}",
            "",
            "If your design cannot fit this budget, simplify or split it; "
            "otherwise upgrade the difficulty tier.",
        ]
    )


def _range_text(low: int, high: int) -> str:
    if low == high:
        return f"exactly {low}"
    if high >= 99:
        return f"≥ {low}"
    return f"{low}–{high}"


def _render_pinned_values(task: DesignTask) -> str:
    # Hard-coded copies of the fields the validator compares for equality
    # against the parent design task. These are the exact strings/numbers
    # the agent must echo into `challenges[0]`; any drift fails the attempt.
    lines = [
        "These values are validated by exact match against the database.",
        "Any drift (even cosmetic) fails the attempt.",
        "",
        f"- `challenges[0].id` = `{task.challenge_id}`",
        f"- `challenges[0].category` = `{task.category}`",
        f"- `challenges[0].difficulty` = `{task.difficulty}`",
        f"- `challenges[0].points` = {task.points}",
    ]
    if task.port is not None:
        lines.append(f"- `challenges[0].port` = {task.port}")
        lines.append(
            "- `challenges[0].deployment` MUST include the substring "
            "`docker` (case-insensitive)."
        )
    lines.extend(
        [
            "",
            "Do NOT use the example id `web-0001` from SKILL.md — use the id "
            "pinned above. SKILL.md examples are illustrative, not authoritative.",
        ]
    )
    return "\n".join(lines)


def _render_event_brief(request: GenerationRequest) -> str:
    return "\n".join(
        [
            f"- topic: {request.topic}",
            f"- category: {request.category}",
            f"- target_count: {request.target_count}",
            f"- max_attempts: {request.max_attempts}",
            "- difficulty_distribution: "
            + _stable_json(request.difficulty_distribution),
            "- runtime_constraints: " + _stable_json(request.runtime_constraints),
        ]
    )


def _render_design_task(task: DesignTask) -> str:
    return "\n".join(
        [
            f"- challenge_id: {task.challenge_id}",
            f"- title: {task.title}",
            f"- category: {task.category}",
            f"- difficulty: {task.difficulty}",
            f"- points: {task.points}",
            f"- port: {task.port if task.port is not None else 'null'}",
            f"- primary_technique: {task.primary_technique}",
            f"- learning_objective: {task.learning_objective}",
            f"- scenario: {task.scenario}",
            f"- constraints: {_stable_json(task.constraints)}",
        ]
    )


def _render_findings(findings: Sequence[ResearchFinding]) -> str:
    capped = list(findings[:EVIDENCE_FINDING_LIMIT])
    if not capped:
        return "- (no cited research findings)"
    lines: list[str] = []
    for index, finding in enumerate(capped, start=1):
        lines.append(
            f"- {index}. [{finding.kind}] {finding.label}: {finding.summary}"
        )
    if len(findings) > EVIDENCE_FINDING_LIMIT:
        lines.append(
            f"- (evidence capped at {EVIDENCE_FINDING_LIMIT} of {len(findings)} findings)"
        )
    return "\n".join(lines)


def _render_sources(sources: Sequence[ResearchSource]) -> str:
    if not sources:
        return "- (no research sources)"
    return "\n".join(
        f"- {source.url} - {source.title}: {source.summary}" for source in sources
    )


def _render_reference(path: str, text: str) -> str:
    body = text.strip()
    if len(body) > MAX_REFERENCE_CHARS:
        body = (
            body[:MAX_REFERENCE_CHARS].rstrip()
            + "\n\n[reference truncated for command-line safety]"
        )
    return f"### @{path}\n\n{body}"


def _stable_json(value: Mapping) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
