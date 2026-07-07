from __future__ import annotations

import pytest

from domain.design.profile_taxonomy import (
    ADVISORY_HISTORY_STATES,
    HARD_OCCUPANCY_STATES,
    DesignDiversityExhausted,
    GovernedProfile,
    ProfileOccupancy,
    ProfilePolicy,
    ProfileTaxonomyError,
    allocate_profile_batch,
    canonical_profile_signatures,
    load_profile_policy,
    normalize_semantic_assignment,
    profile_capacity_check,
    taxonomy_for_category,
    validate_profile,
    validate_profile_policy,
)


def _re_semantic(sub_technique: str = "xor_transform") -> dict[str, str]:
    return {"family": "crackme", "sub_technique": sub_technique}


def _profile(
    *,
    language: str = "rust",
    runtime: str = "linux_amd64",
    artifact_format: str = "elf",
    concealment: str = "runtime_derived_key",
    action: str = "derive_key",
    scenario_type: str = "crackme",
) -> GovernedProfile:
    return GovernedProfile(
        semantic=_re_semantic(),
        solve={
            "analysis_mode": "static",
            "required_action": action,
            "chain_shape": "inspect-derive-submit",
            "required_tool_class": "disassembler",
        },
        implementation={
            "artifact_format": artifact_format,
            "language": language,
            "runtime": runtime,
            "interaction": "argv",
            "control_structure": "linear_checks",
            "flag_concealment": concealment,
        },
        presentation={
            "scenario_type": scenario_type,
            "input_model": "license_key",
        },
    )


def _permissive_policy() -> ProfilePolicy:
    return ProfilePolicy(
        category="re",
        version=1,
        quota_ratios={},
        cooldowns={},
        compatibility={
            "implementation": (
                {
                    "implementation.language": "rust",
                    "implementation.runtime": "linux_amd64",
                    "implementation.artifact_format": "elf",
                },
                {
                    "implementation.language": "rust",
                    "implementation.runtime": "wasm_runtime",
                    "implementation.artifact_format": "wasm",
                },
                {
                    "implementation.language": "java",
                    "implementation.runtime": "jvm",
                    "implementation.artifact_format": "jar",
                },
            )
        },
        hard_forbidden_combined_signatures=frozenset(),
        hard_exclusive_signature=(
            "semantic.family",
            "semantic.sub_technique",
            "solve.analysis_mode",
            "solve.required_action",
            "solve.chain_shape",
            "solve.required_tool_class",
            "implementation.artifact_format",
            "implementation.language",
            "implementation.runtime",
            "implementation.interaction",
            "implementation.control_structure",
            "implementation.flag_concealment",
        ),
    )


def test_validate_profile_rejects_unknown_closed_vocabulary_value() -> None:
    payload = _profile().as_mapping()
    payload["implementation"]["language"] = "zig"

    with pytest.raises(ProfileTaxonomyError, match="closed vocabulary"):
        validate_profile(taxonomy_for_category("re"), payload)


def test_policy_values_cannot_reference_unknown_vocabulary_entries() -> None:
    bad_policy = ProfilePolicy(
        category="re",
        version=1,
        quota_ratios={},
        cooldowns={},
        compatibility={
            "implementation": (
                {
                    "implementation.language": "zig",
                    "implementation.runtime": "linux_amd64",
                    "implementation.artifact_format": "elf",
                },
            )
        },
        hard_forbidden_combined_signatures=frozenset(),
        hard_exclusive_signature=("implementation.language",),
    )

    with pytest.raises(ProfileTaxonomyError, match="unknown value"):
        validate_profile_policy(taxonomy_for_category("re"), bad_policy)


def test_generation_profile_policy_loads_and_validates_repo_config() -> None:
    policy = load_profile_policy("re")

    assert policy.version == 1
    assert "implementation.language" in policy.hard_exclusive_signature
    assert policy.compatibility["implementation"]


def test_canonical_profile_signatures_are_deterministic_and_split_by_axis() -> None:
    profile = _profile()

    left = canonical_profile_signatures(profile, category="re", policy_version=1)
    right = canonical_profile_signatures(profile.as_mapping(), category="re", policy_version=1)
    changed_presentation = canonical_profile_signatures(
        _profile(scenario_type="malware_triage"),
        category="re",
        policy_version=1,
    )

    assert left == right
    assert left.presentation_signature != changed_presentation.presentation_signature
    assert left.combined_profile_signature == changed_presentation.combined_profile_signature
    assert left.exact_signature != changed_presentation.exact_signature


def test_allocator_is_deterministic_and_respects_compatibility() -> None:
    policy = _permissive_policy()
    first = allocate_profile_batch(
        category="re",
        target_count=3,
        semantic_assignments=[_re_semantic()],
        policy=policy,
    )
    second = allocate_profile_batch(
        category="re",
        target_count=3,
        semantic_assignments=[_re_semantic()],
        policy=policy,
    )

    assert [item.signatures.exact_signature for item in first] == [
        item.signatures.exact_signature for item in second
    ]
    assert all(
        {
            "implementation.language": item.profile.implementation["language"],
            "implementation.runtime": item.profile.implementation["runtime"],
            "implementation.artifact_format": item.profile.implementation["artifact_format"],
        }
        in policy.compatibility["implementation"]
        for item in first
    )


def test_exact_combined_signature_hard_occupancy_blocks_reuse() -> None:
    policy = _permissive_policy()
    existing = ProfileOccupancy(profile=_profile(), state="published", source_id="old-1")

    allocated = allocate_profile_batch(
        category="re",
        target_count=1,
        semantic_assignments=[_re_semantic()],
        policy=policy,
        existing=[existing],
    )

    assert allocated[0].signatures.combined_profile_signature != canonical_profile_signatures(
        existing.profile,
        category="re",
        policy_version=1,
    ).combined_profile_signature


def test_advisory_history_does_not_consume_hard_capacity() -> None:
    policy = _permissive_policy()
    advisory = ProfileOccupancy(profile=_profile(), state="design_unbuildable", source_id="old-1")

    allocated = allocate_profile_batch(
        category="re",
        target_count=1,
        semantic_assignments=[_re_semantic()],
        policy=policy,
        existing=[advisory],
    )

    assert allocated[0].signatures.combined_profile_signature == canonical_profile_signatures(
        advisory.profile,
        category="re",
        policy_version=1,
    ).combined_profile_signature
    assert advisory.is_advisory_history is True
    assert advisory.consumes_hard_capacity is False


def test_presentation_cooldown_prefers_non_repeated_scenarios() -> None:
    policy = ProfilePolicy(
        category="re",
        version=1,
        quota_ratios={},
        cooldowns={"presentation.scenario_type": 1},
        compatibility=_permissive_policy().compatibility,
        hard_forbidden_combined_signatures=frozenset(),
        hard_exclusive_signature=_permissive_policy().hard_exclusive_signature,
    )

    allocated = allocate_profile_batch(
        category="re",
        target_count=2,
        semantic_assignments=[_re_semantic()],
        policy=policy,
    )

    assert allocated[0].profile.presentation["scenario_type"] != allocated[1].profile.presentation[
        "scenario_type"
    ]


def test_hard_and_advisory_occupancy_state_sets_are_disjoint() -> None:
    assert HARD_OCCUPANCY_STATES.isdisjoint(ADVISORY_HISTORY_STATES)


def test_capacity_check_reports_design_diversity_exhausted() -> None:
    policy = ProfilePolicy(
        category="re",
        version=1,
        quota_ratios={},
        cooldowns={},
        compatibility={
            "implementation": (
                {
                    "implementation.language": "rust",
                    "implementation.runtime": "linux_amd64",
                    "implementation.artifact_format": "elf",
                },
            )
        },
        hard_forbidden_combined_signatures=frozenset(),
        hard_exclusive_signature=("semantic.family", "semantic.sub_technique"),
    )
    result = profile_capacity_check(
        category="re",
        target_count=2,
        semantic_assignments=[_re_semantic()],
        policy=policy,
    )

    assert result.can_allocate is False
    assert result.diagnostics["code"] == "design_diversity_exhausted"
    with pytest.raises(DesignDiversityExhausted):
        allocate_profile_batch(
            category="re",
            target_count=2,
            semantic_assignments=[_re_semantic()],
            policy=policy,
        )


def test_single_value_quota_dimension_does_not_exhaust_capacity() -> None:
    policy = load_profile_policy("web")

    result = profile_capacity_check(
        category="web",
        target_count=3,
        semantic_assignments=[
            {"family": "injection", "sub_technique": "blind sqli"},
            {"family": "client_side", "sub_technique": "dom xss"},
            {"family": "server_side", "sub_technique": "ssrf"},
        ],
        policy=policy,
    )

    assert result.can_allocate is True
    assert {item.profile.implementation["artifact_format"] for item in result.allocations} == {
        "container"
    }


def test_quota_caps_keep_low_cardinality_dimensions_allocatable() -> None:
    result = profile_capacity_check(
        category="pwn",
        target_count=25,
        semantic_assignments=[
            {"family": "format_string", "sub_technique": "format_string_got"}
        ],
    )

    assert result.can_allocate is True
    assert len(result.allocations) == 25


def test_profile_capacity_preserves_freeform_subtechniques() -> None:
    result = profile_capacity_check(
        category="pwn",
        target_count=1,
        semantic_assignments=[
            {
                "family": "format_string",
                "sub_technique": "64 bit stack offset determination",
            }
        ],
    )

    assert result.can_allocate is True
    assert result.allocations[0].profile.semantic == {
        "family": "format_string",
        "sub_technique": "64_bit_stack_offset_determination",
    }


def test_profile_capacity_coerces_canary_buffer_overflow_phrase() -> None:
    result = profile_capacity_check(
        category="pwn",
        target_count=1,
        semantic_assignments=[
            {
                "family": "stack",
                "sub_technique": "canary leak then buffer overflow",
            }
        ],
    )

    assert result.can_allocate is True
    assert result.allocations[0].profile.semantic == {
        "family": "stack",
        "sub_technique": "ret2libc",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("buffer overflow", "ret2libc"),
        ("stack overflow", "ret2libc"),
        ("canary leak", "ret2libc"),
        ("stack canary leak", "ret2libc"),
        ("ret2plt", "ret2libc"),
        ("ret2csu flow", "ret2csu"),
        ("ret2csu gadget part 1", "ret2csu"),
        ("ret2dlresolve", "ret2dlresolve"),
        ("ret2dlresolve fake relocation", "ret2dlresolve"),
        ("dl runtime resolve exploitation", "ret2dlresolve"),
        ("stack pivot", "stack_pivot"),
        ("stack pivot with leave ret gadget", "stack_pivot"),
        ("partial overwrite for pivot", "stack_pivot"),
        ("one_gadget", "ret2libc"),
        ("unlink attack", "heap_uaf_tcache"),
        ("tcache poisoning", "heap_uaf_tcache"),
        ("uaf", "heap_uaf_tcache"),
        ("format string", "format_string_got"),
    ],
)
def test_pwn_aliases_normalize_to_closed_vocabulary(raw: str, expected: str, caplog) -> None:
    with caplog.at_level("WARNING"):
        semantic = normalize_semantic_assignment(
            taxonomy_for_category("pwn"),
            {"family": "stack", "sub_technique": raw},
        )

    assert semantic["sub_technique"] == expected
    if raw != expected:
        assert f"normalized={expected!r}" in caplog.text


def test_pwn_stack_variants_keep_profile_capacity_distinct() -> None:
    result = profile_capacity_check(
        category="pwn",
        target_count=6,
        semantic_assignments=[
            {"family": "stack", "sub_technique": "basic ret2libc"},
            {"family": "format_string", "sub_technique": "got overwrite"},
            {"family": "stack", "sub_technique": "ret2csu flow"},
            {"family": "stack", "sub_technique": "ret2dlresolve"},
            {"family": "stack", "sub_technique": "stack pivot"},
            {"family": "stack", "sub_technique": "ret2win"},
        ],
    )

    assert result.can_allocate is True
    assert [item.profile.semantic["sub_technique"] for item in result.allocations] == [
        "ret2libc",
        "format_string_got",
        "ret2csu",
        "ret2dlresolve",
        "stack_pivot",
        "ret2win",
    ]


def test_unknown_subtechnique_is_preserved_as_open_semantic_key() -> None:
    semantic = normalize_semantic_assignment(
        taxonomy_for_category("pwn"),
        {"family": "stack", "sub_technique": "rainbow table"},
    )

    assert semantic == {"family": "stack", "sub_technique": "rainbow table"}


def test_unsupported_pwn_profile_is_rejected_before_random_solve_allocation() -> None:
    result = profile_capacity_check(
        category="pwn",
        target_count=1,
        semantic_assignments=[
            {"family": "stack", "sub_technique": "rainbow table"}
        ],
    )

    assert result.can_allocate is False
    assert result.diagnostics["code"] == "unsupported_pwn_profile"
    assert result.diagnostics["semantic"] == {
        "family": "stack",
        "sub_technique": "rainbow table",
    }
    with pytest.raises(DesignDiversityExhausted) as exc_info:
        allocate_profile_batch(
            category="pwn",
            target_count=1,
            semantic_assignments=[
                {"family": "stack", "sub_technique": "rainbow table"}
            ],
        )
    assert exc_info.value.code == "unsupported_pwn_profile"


def test_pwn_format_string_freeform_subtechniques_slug_instead_of_collapsing() -> None:
    cases = {
        "64 bit stack offset determination": "64_bit_stack_offset_determination",
        "GOT overwrite with %n": "got_overwrite_with_n",
        "Format string with stack pivot": "format_string_with_stack_pivot",
        "byte by byte leak": "byte_by_byte_leak",
    }

    normalized = [
        normalize_semantic_assignment(
            taxonomy_for_category("pwn"),
            {"family": "format_string", "sub_technique": raw},
        )
        for raw in cases
    ]

    assert [item["sub_technique"] for item in normalized] == list(cases.values())
    assert "format_string_got" not in {item["sub_technique"] for item in normalized}


def test_profile_capacity_preserves_pwn_format_string_freeform_subtechniques() -> None:
    result = profile_capacity_check(
        category="pwn",
        target_count=3,
        semantic_assignments=[
            {
                "family": "format_string",
                "sub_technique": "64 bit stack offset determination",
            },
            {"family": "format_string", "sub_technique": "GOT overwrite with %n"},
            {
                "family": "format_string",
                "sub_technique": "Format string with stack pivot",
            },
        ],
    )

    subtechniques = [
        item.profile.semantic["sub_technique"] for item in result.allocations
    ]
    assert result.can_allocate is True
    assert subtechniques == [
        "64_bit_stack_offset_determination",
        "got_overwrite_with_n",
        "format_string_with_stack_pivot",
    ]
    assert len(set(subtechniques)) == 3
    assert set(subtechniques) != {"format_string_got"}


def test_capacity_check_accepts_new_subtechniques_for_any_category() -> None:
    result = profile_capacity_check(
        category="web",
        target_count=3,
        semantic_assignments=[
            {"family": "injection", "sub_technique": "graphql alias batching"},
            {"family": "server_side", "sub_technique": "metadata proxy smuggling"},
            {"family": "client_side", "sub_technique": "postmessage origin confusion"},
        ],
    )

    assert result.can_allocate is True
    assert [item.profile.semantic["sub_technique"] for item in result.allocations] == [
        "graphql alias batching",
        "metadata proxy smuggling",
        "postmessage origin confusion",
    ]


def test_capacity_check_requires_semantic_assignments() -> None:
    with pytest.raises(ProfileTaxonomyError, match="semantic_assignments must not be empty"):
        profile_capacity_check(category="re", target_count=1, semantic_assignments=[])


def test_policy_compatibility_prevents_silent_c_elf_fallback() -> None:
    wasm_policy = ProfilePolicy(
        category="re",
        version=1,
        quota_ratios={},
        cooldowns={},
        compatibility={
            "implementation": (
                {
                    "implementation.language": "rust",
                    "implementation.runtime": "wasm_runtime",
                    "implementation.artifact_format": "wasm",
                },
            )
        },
        hard_forbidden_combined_signatures=frozenset(),
        hard_exclusive_signature=(
            "semantic.family",
            "semantic.sub_technique",
            "solve.analysis_mode",
            "solve.required_action",
            "solve.chain_shape",
            "solve.required_tool_class",
            "implementation.artifact_format",
            "implementation.language",
            "implementation.runtime",
            "implementation.interaction",
            "implementation.control_structure",
            "implementation.flag_concealment",
        ),
    )

    allocated = allocate_profile_batch(
        category="re",
        target_count=1,
        semantic_assignments=[_re_semantic()],
        policy=wasm_policy,
    )

    assert allocated[0].profile.implementation["artifact_format"] == "wasm"
    assert allocated[0].profile.implementation["runtime"] == "wasm_runtime"
    assert allocated[0].profile.implementation["language"] == "rust"
