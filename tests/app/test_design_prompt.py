"""Unit tests for structured challenge design prompt assembly."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
from uuid import uuid4

from core.paths import ProjectPaths
from domain.design_tasks import DesignTask
from domain.research import GenerationRequest, ResearchFinding, ResearchSource
from services.design_prompt import (
    EVIDENCE_FINDING_LIMIT,
    MAX_REFERENCE_CHARS,
    build_design_prompt,
    load_design_prompt_context,
)


def _write_prompt_files(paths: ProjectPaths) -> None:
    paths.design_skill.parent.mkdir(parents=True, exist_ok=True)
    paths.design_skill.write_text(
        """
# Design Skill

For machine-readable output, use this JSON shape:
{"event": {"flag_format": "flag{...}"}, "challenges": [{"id": "web-01"}]}
""".strip(),
        encoding="utf-8",
    )
    paths.design_references.mkdir(parents=True, exist_ok=True)
    for name in (
        "design-core.md",
        "category-tactics.md",
    ):
        paths.design_references.joinpath(name).write_text(
            f"# {name}\nreference body for {name}\n",
            encoding="utf-8",
        )


def _paths(tmp_path) -> ProjectPaths:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    _write_prompt_files(paths)
    return paths


def _request(category: str = "web") -> GenerationRequest:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return GenerationRequest(
        id=uuid4(),
        category=category,
        topic="JWT key confusion",
        target_count=1,
        difficulty_distribution=MappingProxyType({"medium": 1}),
        runtime_constraints=MappingProxyType({"docker_required": True}),
        seed_urls=(),
        max_attempts=3,
        status="researched",
        created_at=now,
        updated_at=now,
    )


def _task(category: str = "web") -> DesignTask:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return DesignTask(
        id=uuid4(),
        generation_request_id=uuid4(),
        research_run_id=uuid4(),
        task_no=1,
        challenge_id=f"{category}-0001",
        title="Key Confusion",
        category=category,
        difficulty="medium",
        primary_technique="JWT kid path traversal",
        learning_objective="Inspect token key selection boundaries",
        points=300,
        port=8080 if category in {"web", "pwn"} else None,
        scenario="Internal note service",
        constraints=MappingProxyType({"single_service": True}),
        evidence_summary="JWT research summary",
        finding_ids=(),
        status="queued",
        created_at=now,
        updated_at=now,
    )


def _findings(count: int) -> list[ResearchFinding]:
    return [
        ResearchFinding(
            id=uuid4(),
            research_run_id=uuid4(),
            kind="technique",
            label=f"finding-{index:02d}",
            summary=f"summary {index}",
        )
        for index in range(1, count + 1)
    ]


def _sources() -> list[ResearchSource]:
    return [
        ResearchSource(
            id=uuid4(),
            research_run_id=uuid4(),
            url="https://example.test/reference",
            title="Reference",
            summary="Reference summary",
            content_hash="0" * 64,
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]


def test_build_design_prompt_is_byte_identical_for_identical_inputs(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))
    args = (context, _task(), _request(), _findings(2), _sources())

    first = build_design_prompt(*args)
    second = build_design_prompt(*args)

    assert first == second
    assert "/skill design-challenges" in first
    assert "For machine-readable output" in first
    assert '"challenges"' in first


def test_prompt_injects_unified_references_for_every_category(tmp_path):
    """After the 9→3 ref collapse the prompt no longer routes by category.

    design-core (output + quality gate) and category-tactics (one table for
    every category, including web/pwn/reverse and crypto/forensics/misc) are
    always injected. delivery-format.md was moved out of the skill into
    docs/delivery-formats/ and must not appear in any prompt.
    """
    context = load_design_prompt_context(_paths(tmp_path))

    for category in ("web", "pwn", "re", "crypto", "forensics"):
        prompt = build_design_prompt(
            context, _task(category), _request(category), [], []
        )
        assert "@skills/design-challenges/references/design-core.md" in prompt
        assert "@skills/design-challenges/references/category-tactics.md" in prompt
        # Legacy split references must be gone for every category.
        for legacy in (
            "web-design.md",
            "pwn-design.md",
            "reverse-design.md",
            "other-categories.md",
            "spec-template.md",
            "quality-gate.md",
            "delivery-format.md",
            "glm5-generation.md",
        ):
            assert f"references/{legacy}" not in prompt, legacy


def test_evidence_cap_preserves_insertion_order(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))
    findings = _findings(EVIDENCE_FINDING_LIMIT + 5)

    prompt = build_design_prompt(context, _task(), _request(), findings, _sources())

    assert "finding-01" in prompt
    assert f"finding-{EVIDENCE_FINDING_LIMIT:02d}" in prompt
    assert f"finding-{EVIDENCE_FINDING_LIMIT + 1:02d}" not in prompt
    assert f"evidence capped at {EVIDENCE_FINDING_LIMIT}" in prompt


def test_prompt_includes_always_on_references_and_contract(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))

    task = _task()
    prompt = build_design_prompt(context, task, _request(), [], [])

    assert "@skills/design-challenges/references/design-core.md" in prompt
    assert "@skills/design-challenges/references/category-tactics.md" in prompt
    # Hardened Output Contract (post split-design-tasks fix #2):
    assert "SINGLE JSON object and nothing else" in prompt
    assert "`challenges` MUST be an array of length 1" in prompt
    assert "Do NOT write the JSON to a file" in prompt


def test_prompt_pins_parent_values_verbatim(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))

    task = _task()
    prompt = build_design_prompt(context, task, _request(), [], [])

    # Pinned Values block must echo the exact parent-task values the
    # validator does equality checks against, so the agent cannot drift
    # to SKILL.md example values like ``web-0001``.
    assert "## Pinned Values" in prompt
    assert f"`challenges[0].id` = `{task.challenge_id}`" in prompt
    assert f"`challenges[0].category` = `{task.category}`" in prompt
    assert f"`challenges[0].difficulty` = `{task.difficulty}`" in prompt
    assert f"`challenges[0].points` = {task.points}" in prompt
    if task.port is not None:
        assert f"`challenges[0].port` = {task.port}" in prompt
        assert "deployment` MUST include the substring `docker`" in prompt


def test_long_references_are_truncated_for_command_line_safety(tmp_path):
    paths = _paths(tmp_path)
    paths.design_skill.write_text("A" * (MAX_REFERENCE_CHARS + 100), encoding="utf-8")
    context = load_design_prompt_context(paths)

    prompt = build_design_prompt(context, _task(), _request(), [], [])

    assert "reference truncated for command-line safety" in prompt
    assert "A" * MAX_REFERENCE_CHARS in prompt
    assert "A" * (MAX_REFERENCE_CHARS + 1) not in prompt
