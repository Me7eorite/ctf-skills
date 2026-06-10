"""Hermes prompt rendering."""

from __future__ import annotations

import sys
from pathlib import Path

from core.paths import ProjectPaths


def render_prompt(paths: ProjectPaths, shard: Path, report: Path, worker: str) -> str:
    prompt = paths.prompt_template.read_text(encoding="utf-8")
    cli_script = Path(__file__).resolve().parents[1] / "cli.py"
    replacements = {
        "{shard_path}": str(shard.resolve()),
        "{challenge_dir}": str(paths.challenges.resolve()),
        "{report_path}": str(report.resolve()),
        "{generation_profile}": str(paths.generation_profile.resolve()),
        "{design_skill}": str(paths.design_skill.resolve()),
        "{design_references}": str(paths.design_references.resolve()),
        "{worker}": worker,
        "{shard_name}": shard.name,
        "{progress_command}": (
            f'"{sys.executable}" "{cli_script}" progress '
            f'--shard "{shard.name}" --worker "{worker}"'
        ),
    }
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt
