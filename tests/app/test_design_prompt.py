"""Unit tests for structured challenge design prompt assembly."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from core.paths import ProjectPaths
from domain.design.difficulty import RUBRIC
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
        "difficulty-rubric.md",
        "shared_generation_strategy.md",
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

    assert "design_evidence.research_finding_ids" in prompt
    assert f"id={findings[0].id}" in prompt
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
    assert "@skills/design-challenges/references/shared_generation_strategy.md" in prompt
    # Phase 4: the Output Contract is now a JSON Schema + 3 invariants.
    assert "## Output Contract" in prompt
    assert "Invariants" in prompt
    assert "SINGLE JSON object" in prompt
    # JSON Schema block — top-level shape must constrain to one challenge.
    assert '"maxItems": 1' in prompt
    assert '"minItems": 1' in prompt
    assert '"required":' in prompt
    # Required artifacts remain enforced via the schema pattern + invariant.
    assert "writenup/wp.md" in prompt
    assert "writenup/exp.py" in prompt
    assert "attachments/crackme" in prompt
    assert "deploy/Makefile" in prompt


def test_pwn_design_prompt_requires_xinetd_artifact(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))

    prompt = build_design_prompt(context, _task("pwn"), _request("pwn"), [], [])

    assert '"service_user"' in prompt
    assert "`implementation_plan.service_user = ctf`" in prompt
    assert "this is the challenge service process user" in prompt
    assert "Ordinary pwn tasks should use the xinetd/chroot service model" in prompt
    assert "Design validation does not reject `root` or `xinetd` here" in prompt
    assert "Build validation and scaffold repair enforce the final runtime user" in prompt
    assert "A small multi-file project is valid" in prompt
    assert "deploy/src/src/main.c" in prompt
    assert "deploy/src/lib/menu.c" in prompt
    assert "not limited to a single `deploy/src/vuln.c`" in prompt
    assert "`deploy/_files/ctf.xinetd` is strongly recommended" in prompt
    assert "Build validation/repair will normalize the xinetd scaffold" in prompt
    assert "pwn/xinetd-chroot" in prompt
    assert "scaffolds/pwn/xinetd-chroot/" in prompt


def test_prompt_includes_previous_validation_error_on_retry(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))

    prompt = build_design_prompt(
        context,
        _task(),
        _request(),
        [],
        [],
        previous_error="invalid entry: 'dist/bad path'",
    )

    assert "## Retry Feedback" in prompt
    assert "invalid entry: 'dist/bad path'" in prompt
    assert "Re-check the complete Output Contract" in prompt


def test_prompt_includes_previous_draft_seed_path(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))

    prompt = build_design_prompt(
        context,
        _task(),
        _request(),
        [],
        [],
        previous_design_seed_path="./state/previous_design.json",
    )

    assert "## Previous Draft Seed" in prompt
    assert "./state/previous_design.json" in prompt


def test_governed_prompt_mentions_closed_harness_contract(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))
    task = _task()
    reservation = {
        "id": str(uuid4()),
        "reservation_version": 1,
        "reserved_profile": {
            "semantic": {"family": "injection", "sub_technique": "sqli"},
            "solve": {
                "analysis_mode": "blackbox",
                "required_action": "payload_injection",
                "chain_shape": "inject-exfiltrate",
                "required_tool_class": "http_client",
            },
            "implementation": {
                "artifact_format": "container",
                "language": "python",
                "runtime": "flask",
                "interaction": "http_form",
                "control_structure": "route_handler",
                "flag_concealment": "database_record",
            },
            "presentation": {
                "scenario_type": "ticket_queue",
                "input_model": "web_form",
            },
        },
        "profile_signature": "sig",
        "taxonomy_version": 1,
        "policy_version": 1,
        "ledger_version": 1,
    }

    prompt = build_design_prompt(
        context,
        task,
        _request(),
        [],
        [],
        reservation=reservation,
        ledger_snapshot={"sibling_entries": [], "historical_entries": []},
    )

    assert "Copy the supplied `reserved_profile` exactly into" in prompt
    assert "build_contract.required_profile" in prompt
    assert "build_contract.required_asset_flow` must be a non-empty array" in prompt
    assert "Harnesses cannot contain `command`" in prompt
    assert '"buildContractHarness"' in prompt
    assert '"buildContractAssetStage"' in prompt


def test_re_prompt_calls_out_strings_and_no_hardcoded_flag(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))

    prompt = build_design_prompt(context, _task("re"), _request("re"), [], [])

    assert "strings on the binary" in prompt
    assert "validate.sh" in prompt
    assert "writenup/exp.py" in prompt
    assert "metadata.flag" in prompt


def test_prompt_renders_build_budget_for_difficulty(tmp_path):
    # Phase 2.5: the prompt MUST quote the per-tier buildability caps so
    # the agent self-constrains to what build can actually finish.
    context = load_design_prompt_context(_paths(tmp_path))

    prompt = build_design_prompt(context, _task(), _request(), [], [])

    assert "## Build Budget" in prompt
    # Medium baseline: techniques 2–3, explicit components ≤ 7, LOC ≤ 400.
    assert "techniques: 2–3" in prompt
    assert "intended_path steps: ≤ 5" in prompt
    assert "intended_path steps: 1–5" not in prompt
    assert "explicit `implementation_plan.components` entries: ≤ 7" in prompt
    assert "upgrade the difficulty tier" in prompt
    assert "LOC" in prompt and "400" in prompt


def test_prompt_and_reference_use_rubric_intended_path_cap(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))
    cap = RUBRIC["medium"].intended_path_max

    prompt = build_design_prompt(context, _task(), _request(), [], [])
    reference = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "design-challenges"
        / "references"
        / "difficulty-rubric.md"
    ).read_text(encoding="utf-8")

    assert f"intended_path steps: ≤ {cap}" in prompt
    assert f"| medium | **2 or 3** | ≤ {cap} |" in reference


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


def test_prior_designs_section_empty_when_first_in_batch(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))
    prompt = build_design_prompt(context, _task(), _request(), [], [], prior_designs=[])
    assert "## Prior Batch Designs" in prompt
    assert "this is the first design in the batch" in prompt


def test_prior_designs_section_lists_siblings_and_warns_against_collapse(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))
    prior = [
        {
            "id": "re-0001",
            "category": "re",
            "difficulty": "easy",
            "primary_technique": "ptrace anti-debug",
            "techniques": ["ptrace anti-debug"],
            "asset_flow_shape": ["debugger-free branch", "xor key"],
            "unintended_solutions": ["bare run"],
        }
    ]
    prompt = build_design_prompt(
        context, _task("re"), _request("re"), [], [], prior_designs=prior
    )
    assert "do NOT reuse the same primary technique" in prompt
    assert "re-0001" in prompt
    assert "ptrace anti-debug" in prompt
    assert "debugger-free branch -> xor key" in prompt


def test_design_digest_extracts_collapse_fields():
    from types import SimpleNamespace

    from services.challenge_design_service import _design_digest

    sib = SimpleNamespace(
        challenge_id="re-0002",
        category="re",
        difficulty="medium",
        primary_technique="fallback",
    )
    challenge = {
        "id": "re-0002",
        "category": "re",
        "difficulty": "medium",
        "primary_technique": "RDTSC timing",
        "techniques": ["RDTSC timing", "key derivation"],
        "asset_flow": [
            {"produced_asset_or_capability": "timing key"},
            {"produced_asset_or_capability": ""},  # filler, ignored
            {"produced_asset_or_capability": "decrypted flag"},
        ],
        "unintended_solutions": ["bare run prints flag"],
    }
    digest = _design_digest(challenge, sib)
    assert digest["id"] == "re-0002"
    assert digest["primary_technique"] == "RDTSC timing"
    assert digest["asset_flow_shape"] == ["timing key", "decrypted flag"]
    assert digest["techniques"] == ["RDTSC timing", "key derivation"]


def test_prompt_renders_advisory_mechanism_vocabulary_not_binding(tmp_path):
    import dataclasses

    context = load_design_prompt_context(_paths(tmp_path))
    task = dataclasses.replace(
        _task("re"),
        diversity_flags={"advisory_mechanism_vocabulary": ["tea_xtea", "vm_check"]},
    )
    prompt = build_design_prompt(context, task, _request("re"), [], [])
    assert "advisory_mechanism_vocabulary" in prompt
    assert "choose the mechanism from the request" in prompt
    assert "chosen_mechanism: MUST be declared by the design model" in prompt
    assert "assigned core_mechanism" not in prompt


def test_governed_prompt_spells_out_server_side_contract_rules(tmp_path):
    context = load_design_prompt_context(_paths(tmp_path))
    reserved_profile = {
        "semantic": {"family": "sql_injection", "sub_technique": "boolean_blind"},
        "solve": {
            "analysis_mode": "dynamic_probe",
            "required_action": "payload_injection",
            "chain_shape": "oracle_to_secret",
            "required_tool_class": "http_client",
        },
        "implementation": {
            "artifact_format": "container",
            "language": "python",
            "runtime": "flask",
            "interaction": "http_form",
            "control_structure": "route_handler",
            "flag_concealment": "database_record",
        },
        "presentation": {
            "scenario_type": "ticket_queue",
            "input_model": "web_form",
        },
    }

    prompt = build_design_prompt(
        context,
        _task(),
        _request(),
        _findings(1),
        _sources(),
        reservation={
            "id": str(uuid4()),
            "reserved_profile": reserved_profile,
            "profile_signature": "sig",
            "taxonomy_version": 1,
            "policy_version": 1,
            "ledger_version": 7,
        },
        ledger_snapshot={
            "ledger_version": 7,
            "reservation_id": str(uuid4()),
            "quota_usage": {},
            "forbidden_signatures": [],
            "sibling_entries": [
                {"challenge_id": "web-0000", "profile_signature": "sib"}
            ],
            "historical_entries": [
                {"challenge_id": "web-old", "profile_signature": "hist"}
            ],
        },
    )

    assert "`build_contract.required_player_actions` MUST include exactly `payload_injection`" in prompt
    assert "`Solve-axis: ...` and `Implementation-axis: ...`" in prompt
    assert "web-0000, web-old" in prompt
    assert "`artifact_direct_run` -> `stdout_not_contains_flag` or `must_fail`" in prompt
    assert "Harnesses cannot contain `command`, `argv`, `shell`, `path`, `cwd`, or `executable`" in prompt
    assert "`build_contract.forbidden_shortcuts` and `build_contract.acceptance_tests` must be arrays of harness objects" in prompt
    assert "use `[]` rather than a string placeholder" in prompt
    assert "`build_contract.required_components` and `build_contract.allowed_implementation_freedom` must be arrays of non-empty strings" in prompt
    assert "Empty arrays are valid; use `[]` when there are no entries" in prompt
    assert '"allowed_implementation_freedom"' in prompt
    assert "Never emit null, a single string, or object entries" in prompt
    assert '"buildContractHarness"' in prompt
    assert '"forbidden_shortcuts"' in prompt
    assert '"$ref": "#/$defs/buildContractHarness"' in prompt
