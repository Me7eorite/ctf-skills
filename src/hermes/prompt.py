"""Hermes prompt rendering."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from core.paths import ProjectPaths
from domain.research import GenerationRequest
from domain.resume import ShardResumePlan

RESEARCH_PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "research_prompt.md"
)


def _render_resume_plan_section(plan: ShardResumePlan | None) -> str:
    if plan is None or not plan.challenges:
        return (
            "No prior progress events for this shard; treat every challenge as a "
            "first-time run and start each one at stage `design`."
        )
    lines: list[str] = []
    for challenge in plan.challenges:
        if challenge.lookup_status == "missing_challenge":
            lines.append(
                f"- {challenge.challenge_id}: directory not found; "
                "start at `design` and create the challenge."
            )
            continue
        if challenge.lookup_status == "ambiguous_challenge":
            lines.append(
                f"- {challenge.challenge_id}: multiple matching directories; "
                "the runner will report validate/failed. Skip authoring."
            )
            continue
        skip_repr = (
            ", ".join(challenge.skipped_stages)
            if challenge.skipped_stages
            else "(none)"
        )
        next_stage = challenge.first_pending_stage or "(all stages already complete)"
        lines.append(
            f"- {challenge.challenge_id}: skip_stages={skip_repr}; "
            f"next_stage={next_stage}"
        )
    return "\n".join(lines)


def render_prompt(
    paths: ProjectPaths,
    shard: Path,
    report: Path,
    worker: str,
    *,
    original_shard_name: str | None = None,
    resume_plan: ShardResumePlan | None = None,
) -> str:
    prompt = paths.prompt_template.read_text(encoding="utf-8")
    cli_script = Path(__file__).resolve().parents[1] / "cli.py"
    progress_shard = original_shard_name or shard.name
    replacements = {
        "{shard_path}": str(shard.resolve()),
        "{challenge_dir}": str(paths.challenges.resolve()),
        "{report_path}": str(report.resolve()),
        "{generation_profile}": str(paths.generation_profile.resolve()),
        "{design_skill}": str(paths.design_skill.resolve()),
        "{design_references}": str(paths.design_references.resolve()),
        "{worker}": worker,
        "{shard_name}": progress_shard,
        "{progress_command}": (
            f'"{sys.executable}" "{cli_script}" progress '
            f'--shard "{progress_shard}" --worker "{worker}"'
        ),
        "{resume_plan}": _render_resume_plan_section(resume_plan),
    }
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


def _render_seed_urls(seed_urls: tuple[str, ...]) -> str:
    if not seed_urls:
        return "  (no seed URLs provided)"
    return "\n".join(f"  - {url}" for url in seed_urls)


def _render_difficulty_distribution(distribution) -> str:
    if not distribution:
        return "(unspecified)"
    return ", ".join(f"{label}={count}" for label, count in distribution.items())


def _render_runtime_constraints(constraints) -> str:
    if not constraints:
        return "{}"
    return json.dumps(dict(constraints), ensure_ascii=False, sort_keys=True)


def _render_worked_example(category: str) -> str:
    sample = {
        "sources": [
            {
                "url": "https://example.com/reference-1",
                "title": f"Example {category} reference",
                "summary": "Brief 1-3 sentence summary of what this source covers.",
                "content_hash": "0" * 64,
            }
        ],
        "findings": [
            {
                "kind": "technique",
                "label": f"Sample technique within {category}",
                "summary": "Brief 1-3 sentence summary of the technique itself.",
                "source_indices": [0],
            }
        ],
    }
    return json.dumps(sample, indent=2, ensure_ascii=False)


def render_research_prompt(generation_request: GenerationRequest) -> str:
    """Render `prompts/research_prompt.md` for one generation request.

    The category is rendered at the top of the prompt body so the Agent reads
    it before anything else. Seed URLs come from `generation_request.seed_urls`
    (persisted at submission time) — never from ephemeral CLI state.
    """
    template = RESEARCH_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    category = generation_request.category
    replacements = {
        "{category}": category,
        "{topic}": generation_request.topic,
        "{target_count}": str(generation_request.target_count),
        "{difficulty_distribution}": _render_difficulty_distribution(
            generation_request.difficulty_distribution
        ),
        "{runtime_constraints}": _render_runtime_constraints(
            generation_request.runtime_constraints
        ),
        "{seed_urls}": _render_seed_urls(generation_request.seed_urls),
        "{worked_example}": _render_worked_example(category),
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template
