"""Hermes 提示词渲染。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from core.jsonio import read_json
from core.paths import ProjectPaths
from domain.research import GenerationRequest
from domain.resume import ShardResumePlan

RESEARCH_PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "research_prompt.md"
)


def _render_resume_plan_section(resume_plan: ShardResumePlan | None) -> str:
    # 中文注释：把断点续跑计划整理成提示词片段，帮助 Agent 判断每道题的下一步。
    if resume_plan is None or not resume_plan.challenges:
        return (
            "No prior progress events for this shard; treat every challenge as a "
            "first-time run and start each one at stage `design`."
        )
    section_lines: list[str] = []
    for challenge in resume_plan.challenges:
        if challenge.lookup_status == "missing_challenge":
            section_lines.append(
                f"- {challenge.challenge_id}: directory not found; "
                "start at `design` and create the challenge."
            )
            continue
        if challenge.lookup_status == "ambiguous_challenge":
            section_lines.append(
                f"- {challenge.challenge_id}: multiple matching directories; "
                "the runner will report validate/failed. Skip authoring."
            )
            continue
        skipped_stage_text = (
            ", ".join(challenge.skipped_stages)
            if challenge.skipped_stages
            else "(none)"
        )
        next_stage_name = challenge.first_pending_stage or "(all stages already complete)"
        section_lines.append(
            f"- {challenge.challenge_id}: skip_stages={skipped_stage_text}; "
            f"next_stage={next_stage_name}"
        )
    return "\n".join(section_lines)


def render_prompt(
    paths: ProjectPaths,
    shard: Path,
    report: Path,
    worker: str,
    *,
    report_runtime_path: str | None = None,
    workspace_relative: bool = False,
    original_shard_name: str | None = None,
    resume_plan: ShardResumePlan | None = None,
) -> str:
    # 中文注释：读取分片执行模板，并替换路径、worker、进度命令等运行上下文。
    prompt_text = paths.prompt_template.read_text(encoding="utf-8")
    progress_shard_name = original_shard_name or shard.name
    design_context_instruction = _design_context_instruction(shard)
    if workspace_relative:
        runtime_paths = {
            "{shard_path}": "./input/shard.json",
            "{challenge_dir}": "./output/challenges",
            "{report_path}": "./logs/report.json",
            "{generation_profile}": "./input/generation-profiles.json",
            "{design_skill}": "./references/design-challenges/SKILL.md",
            "{design_references}": "./references/design-challenges/references",
            "{progress_command}": "./bin/progress",
        }
    else:
        cli_script_path = Path(__file__).resolve().parents[1] / "cli.py"
        runtime_paths = {
            "{shard_path}": str(shard.resolve()),
            "{challenge_dir}": str(paths.challenges.resolve()),
            "{report_path}": report_runtime_path or str(report.resolve()),
            "{generation_profile}": str(paths.generation_profile.resolve()),
            "{design_skill}": str(paths.design_skill.resolve()),
            "{design_references}": str(paths.design_references.resolve()),
            "{progress_command}": (
                f'"{sys.executable}" "{cli_script_path}" progress '
                f'--shard "{progress_shard_name}" --worker "{worker}" --best-effort'
            ),
        }
    replacement_map = {
        **runtime_paths,
        "{worker}": worker,
        "{shard_name}": progress_shard_name,
        "{resume_plan}": _render_resume_plan_section(resume_plan),
        "{design_context_instruction}": design_context_instruction,
    }
    for placeholder, rendered_value in replacement_map.items():
        prompt_text = prompt_text.replace(placeholder, rendered_value)
    return prompt_text


def _design_context_instruction(shard: Path) -> str:
    payload = read_json(shard, {})
    challenges = payload.get("challenges") if isinstance(payload, dict) else None
    if not isinstance(challenges, list) or not challenges:
        return ""
    if not all(isinstance(item, dict) and isinstance(item.get("design"), dict) for item in challenges):
        return ""
    return (
        "When each challenge carries a `design` sub-object, use it as "
        "authoritative for deployment, artifacts, flag location, validation "
        "steps, hints, and operator-facing prompt copy."
    )


def _render_seed_urls(seed_urls: tuple[str, ...]) -> str:
    # 中文注释：把持久化的种子 URL 渲染成列表；为空时给出明确占位说明。
    if not seed_urls:
        return "  (no seed URLs provided)"
    return "\n".join(f"  - {url}" for url in seed_urls)


def _render_difficulty_distribution(difficulty_distribution) -> str:
    # 中文注释：把难度分布映射压缩成易读的一行文本，方便 Agent 快速理解目标配比。
    if not difficulty_distribution:
        return "(unspecified)"
    return ", ".join(f"{label}={count}" for label, count in difficulty_distribution.items())


def _render_runtime_constraints(runtime_constraints) -> str:
    # 中文注释：运行约束以稳定 JSON 字符串输出，避免字典顺序导致提示词抖动。
    if not runtime_constraints:
        return "{}"
    return json.dumps(dict(runtime_constraints), ensure_ascii=False, sort_keys=True)


def _render_worked_example(category: str) -> str:
    # 中文注释：生成一个随 category 变化的示例，证明提示词不硬编码初始分类集合。
    example_payload = {
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
    return json.dumps(example_payload, indent=2, ensure_ascii=False)


def render_research_prompt(generation_request: GenerationRequest) -> str:
    """为单个 generation request 渲染 `prompts/research_prompt.md`。

    category 会出现在提示词正文顶部，保证 Agent 在其他信息之前先读到范围约束。
    seed URLs 来自提交时持久化的 `generation_request.seed_urls`，不依赖 CLI 临时状态。
    """
    # 中文注释：从已持久化的 generation_request 渲染 Research Agent 的完整提示词。
    prompt_template = RESEARCH_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    category_code = generation_request.category
    replacement_map = {
        "{category}": category_code,
        "{topic}": generation_request.topic,
        "{target_count}": str(generation_request.target_count),
        "{difficulty_distribution}": _render_difficulty_distribution(
            generation_request.difficulty_distribution
        ),
        "{runtime_constraints}": _render_runtime_constraints(
            generation_request.runtime_constraints
        ),
        "{seed_urls}": _render_seed_urls(generation_request.seed_urls),
        "{worked_example}": _render_worked_example(category_code),
    }
    for placeholder, rendered_value in replacement_map.items():
        prompt_template = prompt_template.replace(placeholder, rendered_value)
    return prompt_template
