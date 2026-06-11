"""Hermes prompt rendering."""

from __future__ import annotations

import sys
from pathlib import Path

from core.paths import ProjectPaths
from domain.resume import ShardResumePlan


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
