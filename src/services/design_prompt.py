"""Prompt assembly for structured challenge design attempts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from core.paths import ProjectPaths
from domain.design_tasks import DesignTask
from domain.research import GenerationRequest, ResearchFinding, ResearchSource

# Phase 1 (9-references → 3): the design skill is now a single core file
# plus a unified category-tactics catalog. cve-pivot.md is read on-demand by
# the agent, not injected into every design prompt. delivery-format moved to
# docs/delivery-formats/ and is no longer part of design.
ALWAYS_REFERENCE_FILES: tuple[str, ...] = ("design-core.md", "category-tactics.md")
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
        "## Output Contract",
        (
            "You MUST reply with a SINGLE JSON object and nothing else.\n"
            "\n"
            "Hard rules (the executor parses your reply verbatim):\n"
            "- The first character of your reply MUST be `{` and the last MUST "
            "be `}`.\n"
            "- Do NOT wrap the JSON in markdown, code fences, headings, tables, "
            "prose, or commentary.\n"
            "- Do NOT write the JSON to a file. Do NOT call Write/Edit/Bash to "
            "create `*.json`, `*.md`, or any other output artifact. Any files "
            "you produce are ignored — only this reply is consumed.\n"
            "- The object MUST match the machine-readable shape from SKILL.md "
            "and MUST contain top-level keys `event` and `challenges`.\n"
            "- `challenges` MUST be an array of length 1.\n"
            "- Echo or default `event.flag_format` and include every field the "
            "validator enforces.\n"
            "- `challenges[0].artifacts` MUST be an array of local "
            "challenge-directory relative file paths, not prose descriptions "
            "and not final delivery zip paths.\n"
            "- Include `README.md`, `metadata.json`, `validate.sh`, "
            "`writenup/wp.md`, and `writenup/exp.py` in `artifacts`.\n"
            "- For web/pwn, also include `deploy/Dockerfile`, "
            "`deploy/docker-compose.yml`, `deploy/src/app.py`, and "
            "`deploy/_files/start.sh` in `artifacts`.\n"
            "- `writenup/wp.md` is the local Chinese writeup source. "
            "`writenup/exp.py` is the local solve script; the packer later "
            "ships it as `exp.py`.\n"
            "- Use `attachments/...` or `dist/...` only for player-facing "
            "attachments.\n"
            "- `validation` MAY mention local compose URLs such as "
            "`http://127.0.0.1:<port>` or `http://localhost:<port>`, but MUST "
            "NOT require external HTTP/HTTPS URLs.\n"
            "- `implementation_plan`, when present, MUST be an intent-level "
            "blueprint only: runtime, framework, service model, entrypoints, "
            "data model, vulnerability location, flag handling, and constraints.\n"
            "- Do NOT include full file contents, code listings, Dockerfile "
            "bodies, docker-compose.yml bodies, SQL scripts, exploit code, "
            "writeup body, README body, or generated source files.\n"
            "- Do NOT include these keys anywhere in `challenges[0]`: "
            "`dockerfile`, `docker_compose`, `dockerfile_snippet`, "
            "`compose_spec`, `source_code`, `app_code`, `init_sql`, "
            "`exploit_sketch`, `exploit_code`, `writeup_body`, `readme_body`, "
            "`files_content`.\n"
            "\n"
            "If you explored with tools, ignore those side artifacts and emit "
            "the final JSON only."
        ),
        "## Pinned Values (copy verbatim into `challenges[0]`)",
        _render_pinned_values(design_task),
    ]
    return "\n\n".join(sections).rstrip() + "\n"


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
