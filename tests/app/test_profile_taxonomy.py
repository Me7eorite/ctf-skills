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


def test_profile_capacity_coerces_freeform_subtechniques_by_family() -> None:
    result = profile_capacity_check(
        category="pwn",
        target_count=1,
        semantic_assignments=[
            {
                "family": "stack",
                "sub_technique": "64 bit stack offset determination",
            }
        ],
    )

    assert result.can_allocate is True
    assert result.allocations[0].profile.semantic == {
        "family": "stack",
        "sub_technique": "ret2libc",
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
