"""Governed design profile vocabulary, policy, signatures, and allocation.

This module is deliberately pure domain code. It does not reserve database
rows, read historical tables, or mutate DesignTask state; later orchestration
layers can call it to validate policy, compute signatures, and ask whether a
batch has enough profile capacity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, Literal

from core.jsonio import read_json
from core.paths import ProjectPaths

LOGGER = logging.getLogger(__name__)

ProfileAxis = Literal["semantic", "solve", "implementation", "presentation"]
PROFILE_AXES: tuple[ProfileAxis, ...] = (
    "semantic",
    "solve",
    "implementation",
    "presentation",
)


class ProfileTaxonomyError(ValueError):
    """Raised when a profile or policy references unknown governed values."""


class DesignDiversityExhausted(ValueError):
    """Raised when no deterministic profile candidate satisfies hard policy."""

    def __init__(self, diagnostics: Mapping[str, Any]) -> None:
        code = str(diagnostics.get("code") or "design_diversity_exhausted")
        super().__init__(code)
        self.code = code
        self.diagnostics = dict(diagnostics)


class ProfileOccupancyState(StrEnum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    LIVE = "live"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    DESIGN_UNBUILDABLE = "design_unbuildable"
    RELEASED = "released"


HARD_OCCUPANCY_STATES: frozenset[str] = frozenset(
    {
        ProfileOccupancyState.RESERVED,
        ProfileOccupancyState.COMMITTED,
        ProfileOccupancyState.LIVE,
        ProfileOccupancyState.PUBLISHED,
    }
)
ADVISORY_HISTORY_STATES: frozenset[str] = frozenset(
    {
        ProfileOccupancyState.SUPERSEDED,
        ProfileOccupancyState.REJECTED,
        ProfileOccupancyState.DESIGN_UNBUILDABLE,
        ProfileOccupancyState.RELEASED,
    }
)


@dataclass(frozen=True)
class AxisSchema:
    fields: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class CategoryProfileTaxonomy:
    category: str
    semantic: AxisSchema
    solve: AxisSchema
    implementation: AxisSchema
    presentation: AxisSchema

    def axis(self, name: ProfileAxis) -> AxisSchema:
        return getattr(self, name)


@dataclass(frozen=True)
class GovernedProfile:
    semantic: Mapping[str, str]
    solve: Mapping[str, str]
    implementation: Mapping[str, str]
    presentation: Mapping[str, str]

    def as_mapping(self) -> dict[str, dict[str, str]]:
        return {
            "semantic": dict(self.semantic),
            "solve": dict(self.solve),
            "implementation": dict(self.implementation),
            "presentation": dict(self.presentation),
        }


@dataclass(frozen=True)
class ProfileSignatures:
    semantic_signature: str
    solve_signature: str
    implementation_signature: str
    presentation_signature: str
    combined_profile_signature: str
    exact_signature: str

    def as_mapping(self) -> dict[str, str]:
        return {
            "semantic_signature": self.semantic_signature,
            "solve_signature": self.solve_signature,
            "implementation_signature": self.implementation_signature,
            "presentation_signature": self.presentation_signature,
            "combined_profile_signature": self.combined_profile_signature,
            "exact_signature": self.exact_signature,
        }


@dataclass(frozen=True)
class PwnSemanticCanonicalization:
    raw_family: str
    raw_sub_technique: str
    canonical_family: str | None
    canonical_sub_technique: str | None
    canonicalization_source: Literal["exact", "alias", "family_fallback", "unsupported"]
    semantic: dict[str, str] | None

    @property
    def supported(self) -> bool:
        return self.semantic is not None

    def as_mapping(self) -> dict[str, Any]:
        return {
            "raw_family": self.raw_family,
            "raw_sub_technique": self.raw_sub_technique,
            "canonical_family": self.canonical_family,
            "canonical_sub_technique": self.canonical_sub_technique,
            "canonicalization_source": self.canonicalization_source,
        }


@dataclass(frozen=True)
class ProfileCandidate:
    profile: GovernedProfile
    signatures: ProfileSignatures
    occupancy_scope: str | None
    exclusive_signature_key: str | None


@dataclass(frozen=True)
class ProfileOccupancy:
    profile: GovernedProfile
    state: str
    source_id: str | None = None

    @property
    def consumes_hard_capacity(self) -> bool:
        return self.state in HARD_OCCUPANCY_STATES

    @property
    def is_advisory_history(self) -> bool:
        return self.state in ADVISORY_HISTORY_STATES


@dataclass(frozen=True)
class ProfileCapacityResult:
    can_allocate: bool
    requested_count: int
    available_count: int
    allocations: tuple[ProfileCandidate, ...]
    diagnostics: Mapping[str, Any]


@dataclass(frozen=True)
class ProfilePolicy:
    category: str
    version: int
    quota_ratios: Mapping[str, float]
    cooldowns: Mapping[str, int]
    compatibility: Mapping[str, tuple[Mapping[str, str], ...]]
    hard_forbidden_combined_signatures: frozenset[str]
    hard_exclusive_signature: tuple[str, ...]


@dataclass(frozen=True)
class _PreparedCandidate:
    candidate: ProfileCandidate
    quota_values: Mapping[str, str]


WEB_TAXONOMY = CategoryProfileTaxonomy(
    category="web",
    semantic=AxisSchema(
        {
            "family": (
                "auth",
                "injection",
                "server_side",
                "client_side",
                "upload",
                "node_api",
            ),
            "sub_technique": (
                "jwt",
                "idor",
                "sqli",
                "xss",
                "ssti",
                "ssrf",
                "path_traversal",
                "deserialization",
                "upload_parse",
                "prototype_pollution",
            ),
        }
    ),
    solve=AxisSchema(
        {
            "analysis_mode": ("blackbox", "source_audit", "hybrid"),
            "required_action": (
                "state_manipulation",
                "payload_injection",
                "credential_forgery",
                "internal_service_reach",
                "file_upload_bypass",
            ),
            "chain_shape": (
                "single-request-exploit",
                "auth-bypass-read",
                "inject-exfiltrate",
                "upload-trigger-read",
                "ssrf-pivot-recover",
            ),
            "required_tool_class": ("browser", "http_client", "proxy", "sql_client"),
        }
    ),
    implementation=AxisSchema(
        {
            "artifact_format": ("container",),
            "language": ("python", "node", "php", "java", "go", "rust"),
            "runtime": (
                "flask",
                "fastapi",
                "express",
                "fastify",
                "plain_php",
                "spring_boot",
                "gin",
                "axum",
            ),
            "interaction": ("http_form", "json_api", "file_upload", "admin_bot"),
            "control_structure": (
                "route_handler",
                "middleware_chain",
                "background_worker",
                "template_render",
            ),
            "flag_concealment": (
                "database_record",
                "signed_token",
                "server_side_file",
                "derived_secret",
            ),
        }
    ),
    presentation=AxisSchema(
        {
            "scenario_type": (
                "admin_portal",
                "reporting_app",
                "ticket_queue",
                "artifact_review",
            ),
            "input_model": ("web_form", "rest_api", "uploaded_file", "browser_workflow"),
        }
    ),
)

PWN_TAXONOMY = CategoryProfileTaxonomy(
    category="pwn",
    semantic=AxisSchema(
        {
            "family": ("stack", "format_string", "heap", "integer_oob", "sandbox"),
            "sub_technique": (
                "ret2libc",
                "ret2win",
                "ret2csu",
                "ret2dlresolve",
                "stack_pivot",
                "format_string_got",
                "heap_uaf_tcache",
                "integer_oob",
                "global_bss_write",
                "seccomp_orw",
            ),
        }
    ),
    solve=AxisSchema(
        {
            "analysis_mode": ("static", "dynamic", "hybrid"),
            "required_action": (
                "leak_address",
                "control_return",
                "write_what_where",
                "heap_groom",
                "syscall_orw",
            ),
            "chain_shape": (
                "leak-rop-shell",
                "overwrite-hook-win",
                "heap-overlap-edit",
                "global-write-win",
                "orw-chain",
            ),
            "required_tool_class": ("debugger", "exploit_script", "rop_tool"),
        }
    ),
    implementation=AxisSchema(
        {
            "artifact_format": ("elf",),
            "language": ("c", "cpp", "rust", "go", "asm"),
            "runtime": ("xinetd_chroot", "stdio_process"),
            "interaction": ("tcp_service", "stdin_stdout", "file_input"),
            "control_structure": ("menu_loop", "parser_state_machine", "callback_table"),
            "flag_concealment": ("privileged_file", "runtime_generated", "post_exploit_read"),
        }
    ),
    presentation=AxisSchema(
        {
            "scenario_type": ("training_service", "legacy_daemon", "sandbox_runner"),
            "input_model": ("line_protocol", "binary_blob", "menu_commands"),
        }
    ),
)

RE_TAXONOMY = CategoryProfileTaxonomy(
    category="re",
    semantic=AxisSchema(
        {
            "family": ("crackme", "vm_bytecode", "runtime", "language", "platform"),
            "sub_technique": (
                "xor_transform",
                "sbox_substitution",
                "bytecode_vm",
                "anti_debug",
                "packed_binary",
                "wasm_lift",
                "language_bytecode",
            ),
        }
    ),
    solve=AxisSchema(
        {
            "analysis_mode": ("static", "dynamic", "symbolic", "hybrid"),
            "required_action": (
                "derive_key",
                "lift_vm",
                "runtime_hook",
                "patch_branch",
                "symbolic_solve",
            ),
            "chain_shape": (
                "inspect-derive-submit",
                "trace-hook-recover",
                "lift-bytecode-solve",
                "patch-run-recover",
            ),
            "required_tool_class": ("disassembler", "debugger", "decompiler", "symbolic_executor"),
        }
    ),
    implementation=AxisSchema(
        {
            "artifact_format": ("elf", "wasm", "jar"),
            "language": ("c", "cpp", "rust", "go", "java", "kotlin"),
            "runtime": ("linux_amd64", "wasm_runtime", "jvm"),
            "interaction": ("argv", "stdin_stdout", "file_input", "gui_state"),
            "control_structure": (
                "linear_checks",
                "callback_graph",
                "bytecode_interpreter",
                "state_machine",
            ),
            "flag_concealment": (
                "runtime_derived_key",
                "table_substitution",
                "symbolic_constraints",
                "packed_section",
            ),
        }
    ),
    presentation=AxisSchema(
        {
            "scenario_type": ("crackme", "malware_triage", "firmware_tool", "game_asset"),
            "input_model": ("license_key", "captured_sample", "config_file", "asset_bundle"),
        }
    ),
)

CATEGORY_PROFILE_TAXONOMIES: Mapping[str, CategoryProfileTaxonomy] = {
    "web": WEB_TAXONOMY,
    "pwn": PWN_TAXONOMY,
    "re": RE_TAXONOMY,
}

_DEFAULT_POLICY_VERSION = 1
_DEFAULT_QUOTA_RATIOS: Mapping[str, float] = {
    "solve.required_action": 0.30,
    "implementation.flag_concealment": 0.20,
    "implementation.language": 0.40,
    "implementation.artifact_format": 0.60,
}
_DEFAULT_COOLDOWNS: Mapping[str, int] = {"presentation.scenario_type": 1}
_DEFAULT_HARD_EXCLUSIVE_SIGNATURE = (
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
)

PWN_CANONICAL_SUB_TECHNIQUE_ALIASES: Mapping[str, str] = {
    "buffer overflow": "ret2libc",
    "stack overflow": "ret2libc",
    "canary leak": "ret2libc",
    "canary leak then buffer overflow": "ret2libc",
    "stack canary leak": "ret2libc",
    "stack canary bypass": "ret2libc",
    "rop": "ret2libc",
    "rop chain": "ret2libc",
    "return oriented programming": "ret2libc",
    "libc leak": "ret2libc",
    "glibc leak": "ret2libc",
    "libc base leak": "ret2libc",
    "ret2plt": "ret2libc",
    "return to plt": "ret2libc",
    "one gadget": "ret2libc",
    "one_gadget": "ret2libc",
    "ret2csu": "ret2csu",
    "ret2csu flow": "ret2csu",
    "ret2dlresolve": "ret2dlresolve",
    "ret2dlresolve fake relocation": "ret2dlresolve",
    "ret2dlresolve with stack pivot": "ret2dlresolve",
    "stack pivot": "stack_pivot",
    "stack pivot with leave ret gadget": "stack_pivot",
    "partial overwrite for pivot": "stack_pivot",
    "pop rsp gadget usage": "stack_pivot",
    "format string": "format_string_got",
    "got overwrite": "format_string_got",
    "bss variable modification": "global_bss_write",
    "bss variable write": "global_bss_write",
    "bss overwrite": "global_bss_write",
    "global variable modification": "global_bss_write",
    "global variable write": "global_bss_write",
    "global overwrite": "global_bss_write",
    "glibc heap": "heap_uaf_tcache",
    "heap": "heap_uaf_tcache",
    "heap exploitation": "heap_uaf_tcache",
    "heap overflow": "heap_uaf_tcache",
    "fastbin": "heap_uaf_tcache",
    "fastbin attack": "heap_uaf_tcache",
    "fastbin dup": "heap_uaf_tcache",
    "unsorted bin": "heap_uaf_tcache",
    "unsorted bin attack": "heap_uaf_tcache",
    "unlink attack": "heap_uaf_tcache",
    "tcache poisoning": "heap_uaf_tcache",
    "tcache dup": "heap_uaf_tcache",
    "use after free": "heap_uaf_tcache",
    "use after free primitive": "heap_uaf_tcache",
    "uaf": "heap_uaf_tcache",
    "integer overflow": "integer_oob",
    "out of bounds": "integer_oob",
    "oob": "integer_oob",
    "seccomp": "seccomp_orw",
    "orw": "seccomp_orw",
}

PWN_CANONICAL_SUB_TECHNIQUE_FAMILY: Mapping[str, str] = {
    "ret2libc": "stack",
    "ret2win": "stack",
    "ret2csu": "stack",
    "ret2dlresolve": "stack",
    "stack_pivot": "stack",
    "format_string_got": "format_string",
    "heap_uaf_tcache": "heap",
    "integer_oob": "integer_oob",
    "global_bss_write": "integer_oob",
    "seccomp_orw": "sandbox",
}

PWN_CANONICAL_FAMILY_DEFAULTS: Mapping[str, tuple[str, str]] = {
    "stack": ("stack", "ret2libc"),
    "format_string": ("format_string", "format_string_got"),
    "heap": ("heap", "heap_uaf_tcache"),
    "integer_oob": ("integer_oob", "integer_oob"),
    "sandbox": ("sandbox", "seccomp_orw"),
}

SUB_TECHNIQUE_ALIASES_BY_CATEGORY: Mapping[str, Mapping[str, str]] = {
    "web": {
        "blind sqli": "sqli",
        "boolean blind sqli": "sqli",
        "second order sqli": "sqli",
        "sql injection": "sqli",
        "sql inj": "sqli",
        "dom xss": "xss",
        "stored xss": "xss",
        "reflected xss": "xss",
        "server side template injection": "ssti",
        "template injection": "ssti",
        "prototype pollution": "prototype_pollution",
        "path traversal": "path_traversal",
        "directory traversal": "path_traversal",
        "file upload bypass": "upload_parse",
        "upload parser bypass": "upload_parse",
        "insecure deserialization": "deserialization",
    },
    "pwn": {
        "buffer overflow": "ret2libc",
        "stack overflow": "ret2libc",
        "canary leak": "ret2libc",
        "canary leak then buffer overflow": "ret2libc",
        "stack canary leak": "ret2libc",
        "stack canary bypass": "ret2libc",
        "ret2plt": "ret2libc",
        "ret2csu": "ret2csu",
        "ret2csu flow": "ret2csu",
        "ret2csu chain continuation": "ret2csu",
        "ret2csu gadget part 1": "ret2csu",
        "ret2csu gadget part 2": "ret2csu",
        "ret2csu one gadget constraint": "ret2csu",
        "ret2csu register alignment": "ret2csu",
        "ret2csu universal gadget": "ret2csu",
        "ret2csu with function pointer": "ret2csu",
        "ret2dlresolve": "ret2dlresolve",
        "ret2dlresolve fake relocation": "ret2dlresolve",
        "ret2dlresolve index calculation": "ret2dlresolve",
        "ret2dlresolve plt call": "ret2dlresolve",
        "ret2dlresolve string placement": "ret2dlresolve",
        "ret2dlresolve symbol name hash": "ret2dlresolve",
        "ret2dlresolve with stack pivot": "ret2dlresolve",
        "dl runtime resolve exploitation": "ret2dlresolve",
        "fake symbol table construction": "ret2dlresolve",
        "pwntools ret2dlresolve helper": "ret2dlresolve",
        "relro bypass with ret2dlresolve": "ret2dlresolve",
        "stack pivot": "stack_pivot",
        "stack pivot with leave ret gadget": "stack_pivot",
        "partial overwrite for pivot": "stack_pivot",
        "pop rsp gadget usage": "stack_pivot",
        "one gadget": "ret2libc",
        "one_gadget": "ret2libc",
        "format string": "format_string_got",
        "format string vulnerability": "format_string_got",
        "got overwrite": "format_string_got",
        "bss variable modification": "global_bss_write",
        "bss variable write": "global_bss_write",
        "bss overwrite": "global_bss_write",
        "global variable modification": "global_bss_write",
        "global variable write": "global_bss_write",
        "global overwrite": "global_bss_write",
        "unlink attack": "heap_uaf_tcache",
        "tcache poisoning": "heap_uaf_tcache",
        "use after free": "heap_uaf_tcache",
        "uaf": "heap_uaf_tcache",
        "integer overflow": "integer_oob",
        "out of bounds": "integer_oob",
        "oob": "integer_oob",
        "seccomp": "seccomp_orw",
        "orw": "seccomp_orw",
    },
    "re": {
        "xor": "xor_transform",
        "xor encoding": "xor_transform",
        "xor encryption": "xor_transform",
        "sbox": "sbox_substitution",
        "s box": "sbox_substitution",
        "bytecode vm": "bytecode_vm",
        "vm bytecode": "bytecode_vm",
        "custom vm": "bytecode_vm",
        "anti debug": "anti_debug",
        "anti debugging": "anti_debug",
        "packed": "packed_binary",
        "packer": "packed_binary",
        "wasm": "wasm_lift",
        "java bytecode": "language_bytecode",
        "python bytecode": "language_bytecode",
    },
}

SUB_TECHNIQUE_FAMILY_DEFAULTS: Mapping[str, str] = {
    "auth": "idor",
    "injection": "sqli",
    "server_side": "ssrf",
    "client_side": "xss",
    "upload": "upload_parse",
    "node_api": "prototype_pollution",
    "stack": "ret2libc",
    "format_string": "format_string_got",
    "heap": "heap_uaf_tcache",
    "integer_oob": "integer_oob",
    "sandbox": "seccomp_orw",
    "crackme": "xor_transform",
    "vm_bytecode": "bytecode_vm",
    "runtime": "anti_debug",
    "language": "language_bytecode",
    "platform": "wasm_lift",
    "visual_game": "xor_transform",
}


def taxonomy_for_category(category: str) -> CategoryProfileTaxonomy:
    try:
        return CATEGORY_PROFILE_TAXONOMIES[category]
    except KeyError as exc:
        raise ProfileTaxonomyError(f"unknown profile category {category!r}") from exc


def load_profile_policy(
    category: str,
    *,
    paths: ProjectPaths | None = None,
    raw_profile: Mapping[str, Any] | None = None,
) -> ProfilePolicy:
    raw = raw_profile if raw_profile is not None else _category_profile_raw(category, paths)
    raw_policy = raw.get("profile_policy") if isinstance(raw, Mapping) else None
    if not isinstance(raw_policy, Mapping):
        raw_policy = {}
    policy = ProfilePolicy(
        category=category,
        version=_positive_int(raw_policy.get("version"), _DEFAULT_POLICY_VERSION),
        quota_ratios=_quota_ratios(raw_policy.get("quota_ratios")),
        cooldowns=_cooldowns(raw_policy.get("cooldowns")),
        compatibility=_compatibility(raw_policy.get("compatibility")),
        hard_forbidden_combined_signatures=frozenset(
            str(item)
            for item in raw_policy.get("hard_forbidden_combined_signatures", ())
            if isinstance(item, str) and item.strip()
        ),
        hard_exclusive_signature=tuple(
            str(item)
            for item in raw_policy.get("hard_exclusive_signature", _DEFAULT_HARD_EXCLUSIVE_SIGNATURE)
            if isinstance(item, str) and item.strip()
        ),
    )
    validate_profile_policy(taxonomy_for_category(category), policy)
    return policy


def validate_profile_policy(
    taxonomy: CategoryProfileTaxonomy,
    policy: ProfilePolicy,
) -> None:
    for path in (*policy.quota_ratios.keys(), *policy.cooldowns.keys(), *policy.hard_exclusive_signature):
        _field_values(taxonomy, path)
    for group_name, rows in policy.compatibility.items():
        if not rows:
            raise ProfileTaxonomyError(f"compatibility group {group_name!r} must not be empty")
        for row in rows:
            for path, value in row.items():
                values = _field_values(taxonomy, path)
                if value not in values:
                    raise ProfileTaxonomyError(
                        f"policy compatibility references unknown value {value!r} for {path}"
                    )


def validate_profile(
    taxonomy: CategoryProfileTaxonomy,
    profile: GovernedProfile | Mapping[str, Any],
) -> GovernedProfile:
    payload = profile.as_mapping() if isinstance(profile, GovernedProfile) else profile
    axes: dict[str, dict[str, str]] = {}
    for axis_name in PROFILE_AXES:
        raw_axis = payload.get(axis_name)
        if not isinstance(raw_axis, Mapping):
            raise ProfileTaxonomyError(f"profile axis {axis_name!r} must be an object")
        schema = taxonomy.axis(axis_name)
        axis_values: dict[str, str] = {}
        missing = sorted(set(schema.fields) - set(raw_axis))
        if missing:
            raise ProfileTaxonomyError(f"profile axis {axis_name!r} missing fields {missing}")
        unknown = sorted(set(raw_axis) - set(schema.fields))
        if unknown:
            raise ProfileTaxonomyError(f"profile axis {axis_name!r} has unknown fields {unknown}")
        for field, allowed in schema.fields.items():
            value = raw_axis.get(field)
            if axis_name == "semantic" and field == "sub_technique":
                normalized = _coerce_sub_technique(
                    _normalize_semantic_value(str(value or "")),
                    allowed,
                    family=str(raw_axis.get("family", "")),
                    category=taxonomy.category,
                )
                if not normalized:
                    raise ProfileTaxonomyError(
                        f"profile {axis_name}.{field}={value!r} must not be empty"
                    )
                axis_values[field] = normalized
                continue
            if not isinstance(value, str) or value not in allowed:
                raise ProfileTaxonomyError(
                    f"profile {axis_name}.{field}={value!r} is not in closed vocabulary"
                )
            axis_values[field] = value
        axes[axis_name] = axis_values
    return GovernedProfile(
        semantic=axes["semantic"],
        solve=axes["solve"],
        implementation=axes["implementation"],
        presentation=axes["presentation"],
    )


def canonical_profile_signatures(
    profile: GovernedProfile | Mapping[str, Any],
    *,
    category: str,
    policy_version: int,
) -> ProfileSignatures:
    normalized = validate_profile(taxonomy_for_category(category), profile)
    payload = normalized.as_mapping()
    semantic = _digest({"category": category, "policy_version": policy_version, "semantic": payload["semantic"]})
    solve = _digest({"category": category, "policy_version": policy_version, "solve": payload["solve"]})
    implementation = _digest(
        {
            "category": category,
            "policy_version": policy_version,
            "implementation": payload["implementation"],
        }
    )
    presentation = _digest(
        {"category": category, "policy_version": policy_version, "presentation": payload["presentation"]}
    )
    combined = _digest(
        {
            "category": category,
            "policy_version": policy_version,
            "semantic": payload["semantic"],
            "solve": payload["solve"],
            "implementation": payload["implementation"],
        }
    )
    exact = _digest({"category": category, "policy_version": policy_version, "profile": payload})
    return ProfileSignatures(
        semantic_signature=semantic,
        solve_signature=solve,
        implementation_signature=implementation,
        presentation_signature=presentation,
        combined_profile_signature=combined,
        exact_signature=exact,
    )


def allocate_profile_batch(
    *,
    category: str,
    target_count: int,
    semantic_assignments: Sequence[Mapping[str, str]],
    policy: ProfilePolicy | None = None,
    existing: Sequence[ProfileOccupancy] = (),
) -> tuple[ProfileCandidate, ...]:
    result = profile_capacity_check(
        category=category,
        target_count=target_count,
        semantic_assignments=semantic_assignments,
        policy=policy,
        existing=existing,
    )
    if not result.can_allocate:
        raise DesignDiversityExhausted(result.diagnostics)
    return result.allocations


def profile_capacity_check(
    *,
    category: str,
    target_count: int,
    semantic_assignments: Sequence[Mapping[str, str]],
    policy: ProfilePolicy | None = None,
    existing: Sequence[ProfileOccupancy] = (),
) -> ProfileCapacityResult:
    if target_count <= 0:
        raise ProfileTaxonomyError("target_count must be positive")
    if not semantic_assignments:
        raise ProfileTaxonomyError("semantic_assignments must not be empty")
    active_policy = policy or load_profile_policy(category)
    taxonomy = taxonomy_for_category(category)
    validate_profile_policy(taxonomy, active_policy)
    hard_existing = [item for item in existing if item.consumes_hard_capacity]
    advisory_existing = [item for item in existing if item.is_advisory_history]
    allocations: list[ProfileCandidate] = []
    exhausted: Counter[str] = Counter()
    existing_signatures = {
        canonical_profile_signatures(
            item.profile,
            category=category,
            policy_version=active_policy.version,
        ).combined_profile_signature
        for item in hard_existing
    }
    existing_exact_keys = {
        _exclusive_key(item.profile, active_policy.hard_exclusive_signature)
        for item in hard_existing
    }
    planned_signatures: set[str] = set()
    planned_exact_keys: set[str] = set()
    quota_caps = _quota_caps(taxonomy, active_policy, target_count)
    quota_counts: dict[str, Counter[str]] = {
        path: Counter(_profile_value(item.profile, path) for item in hard_existing)
        for path in quota_caps
    }
    candidate_cache: dict[
        tuple[tuple[tuple[str, str], ...], int, int, int],
        _PreparedCandidate | None,
    ] = {}

    for index in range(target_count):
        raw_semantic = semantic_assignments[index % len(semantic_assignments)]
        if taxonomy.category == "pwn":
            canonicalization = canonicalize_pwn_semantic_assignment(raw_semantic)
            if not canonicalization.supported or canonicalization.semantic is None:
                return _unsupported_profile_semantic(
                    taxonomy,
                    {
                        "family": canonicalization.raw_family,
                        "sub_technique": canonicalization.raw_sub_technique,
                    },
                    target_count=target_count,
                    allocated_count=len(allocations),
                )
            semantic = canonicalization.semantic
        else:
            semantic = normalize_semantic_assignment(taxonomy, raw_semantic)
        unsupported = _unsupported_profile_semantic(
            taxonomy,
            semantic,
            target_count=target_count,
            allocated_count=len(allocations),
        )
        if unsupported is not None:
            return unsupported
        _validate_axis_values(taxonomy, "semantic", semantic)
        semantic_key = tuple(sorted(semantic.items()))
        candidate = _first_eligible_candidate(
            category=category,
            taxonomy=taxonomy,
            policy=active_policy,
            semantic=semantic,
            semantic_key=semantic_key,
            planned=allocations,
            existing_signatures=existing_signatures,
            existing_exact_keys=existing_exact_keys,
            planned_signatures=planned_signatures,
            planned_exact_keys=planned_exact_keys,
            quota_caps=quota_caps,
            quota_counts=quota_counts,
            quota_paths=tuple(quota_caps),
            candidate_cache=candidate_cache,
        )
        if candidate is None:
            exhausted["candidate_space"] += 1
            diagnostics = {
                "code": "design_diversity_exhausted",
                "category": category,
                "target_count": target_count,
                "allocated_count": len(allocations),
                "available_count": len(allocations),
                "exhausted_dimensions": sorted(exhausted),
                "hard_occupancy_count": len(hard_existing),
                "advisory_history_count": len(advisory_existing),
            }
            return ProfileCapacityResult(
                can_allocate=False,
                requested_count=target_count,
                available_count=len(allocations),
                allocations=tuple(allocations),
                diagnostics=diagnostics,
            )
        allocations.append(candidate)
        planned_signatures.add(candidate.signatures.combined_profile_signature)
        if candidate.exclusive_signature_key:
            planned_exact_keys.add(candidate.exclusive_signature_key)
        for path in quota_caps:
            quota_counts[path][_profile_value(candidate.profile, path)] += 1

    diagnostics = {
        "code": None,
        "category": category,
        "target_count": target_count,
        "allocated_count": len(allocations),
        "hard_occupancy_count": len(hard_existing),
        "advisory_history_count": len(advisory_existing),
    }
    return ProfileCapacityResult(
        can_allocate=True,
        requested_count=target_count,
        available_count=len(allocations),
        allocations=tuple(allocations),
        diagnostics=diagnostics,
    )


def canonicalize_pwn_semantic_assignment(
    semantic: Mapping[str, str],
) -> PwnSemanticCanonicalization:
    raw_family = _normalize_family_value(semantic.get("family", ""))
    raw_sub_technique = _normalize_semantic_value(str(semantic.get("sub_technique", "")))
    if not raw_sub_technique:
        return PwnSemanticCanonicalization(
            raw_family=raw_family or "other",
            raw_sub_technique="",
            canonical_family=None,
            canonical_sub_technique=None,
            canonicalization_source="unsupported",
            semantic=None,
        )

    canonical_sub_technique = _coerce_pwn_canonical_sub_technique(raw_sub_technique)
    if raw_family == "integer_oob" and any(
        token in raw_sub_technique for token in ("bss", "global", "variable", "write")
    ):
        canonical_sub_technique = "global_bss_write"
    canonical_family = PWN_CANONICAL_SUB_TECHNIQUE_FAMILY.get(canonical_sub_technique)
    source: Literal["exact", "alias", "family_fallback", "unsupported"] = "unsupported"
    if (
        canonical_sub_technique == raw_sub_technique
        and canonical_sub_technique in PWN_TAXONOMY.semantic.fields["sub_technique"]
    ):
        source = "exact"
    elif canonical_sub_technique in PWN_TAXONOMY.semantic.fields["sub_technique"]:
        source = "alias"
    else:
        if raw_family == "stack" and not _pwn_stack_supports_freeform(raw_sub_technique):
            return PwnSemanticCanonicalization(
                raw_family=raw_family or "other",
                raw_sub_technique=raw_sub_technique,
                canonical_family=None,
                canonical_sub_technique=None,
                canonicalization_source="unsupported",
                semantic=None,
            )
        if raw_family == "integer_oob" and not _pwn_integer_oob_supports_freeform(raw_sub_technique):
            return PwnSemanticCanonicalization(
                raw_family=raw_family or "other",
                raw_sub_technique=raw_sub_technique,
                canonical_family=None,
                canonical_sub_technique=None,
                canonicalization_source="unsupported",
                semantic=None,
            )
        family_default = PWN_CANONICAL_FAMILY_DEFAULTS.get(raw_family)
        if family_default is not None:
            canonical_family, canonical_sub_technique = family_default
            source = "family_fallback"
        else:
            return PwnSemanticCanonicalization(
                raw_family=raw_family or "other",
                raw_sub_technique=raw_sub_technique,
                canonical_family=None,
                canonical_sub_technique=None,
                canonicalization_source="unsupported",
                semantic=None,
            )

    if canonical_family is None:
        canonical_family = PWN_CANONICAL_SUB_TECHNIQUE_FAMILY.get(canonical_sub_technique)
    if canonical_family is None:
        return PwnSemanticCanonicalization(
            raw_family=raw_family or "other",
            raw_sub_technique=raw_sub_technique,
            canonical_family=None,
            canonical_sub_technique=None,
            canonicalization_source="unsupported",
            semantic=None,
        )

    semantic_payload = normalize_semantic_assignment(
        PWN_TAXONOMY,
        {"family": canonical_family, "sub_technique": canonical_sub_technique},
    )
    return PwnSemanticCanonicalization(
        raw_family=raw_family or "other",
        raw_sub_technique=raw_sub_technique,
        canonical_family=semantic_payload["family"],
        canonical_sub_technique=semantic_payload["sub_technique"],
        canonicalization_source=source,
        semantic=semantic_payload,
    )


def _unsupported_profile_semantic(
    taxonomy: CategoryProfileTaxonomy,
    semantic: Mapping[str, str],
    *,
    target_count: int,
    allocated_count: int,
) -> ProfileCapacityResult | None:
    if taxonomy.category != "pwn":
        return None
    family = semantic.get("family", "")
    sub_technique = semantic.get("sub_technique", "")
    if family in taxonomy.semantic.fields["family"] and sub_technique in taxonomy.semantic.fields["sub_technique"]:
        return None
    diagnostics = {
        "code": "unsupported_pwn_profile",
        "category": taxonomy.category,
        "target_count": target_count,
        "allocated_count": allocated_count,
        "available_count": allocated_count,
        "semantic": dict(semantic),
        "reason": (
            "pwn semantic sub_technique has no governed solve primitive; "
            "map it to a supported primitive before reserving a profile"
        ),
    }
    return ProfileCapacityResult(
        can_allocate=False,
        requested_count=target_count,
        available_count=allocated_count,
        allocations=(),
        diagnostics=diagnostics,
    )


def profile_occupancy_from_mapping(
    profile: Mapping[str, Any],
    *,
    category: str,
    state: str,
    source_id: str | None = None,
) -> ProfileOccupancy:
    return ProfileOccupancy(
        profile=validate_profile(taxonomy_for_category(category), profile),
        state=state,
        source_id=source_id,
    )


def normalize_semantic_assignment(
    taxonomy: CategoryProfileTaxonomy,
    semantic: Mapping[str, str],
) -> dict[str, str]:
    """Map advisory technique labels into the governed closed vocabulary."""

    family = (
        str(semantic.get("family", ""))
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if family not in taxonomy.semantic.fields["family"]:
        family = "other" if "other" in taxonomy.semantic.fields["family"] else family

    raw_sub = _normalize_semantic_value(str(semantic.get("sub_technique", "")))
    allowed = taxonomy.semantic.fields["sub_technique"]
    if raw_sub in allowed:
        sub_technique = raw_sub
    else:
        sub_technique = _coerce_sub_technique(
            raw_sub,
            allowed,
            family=family,
            category=taxonomy.category,
        )
        LOGGER.warning(
            "normalized profile semantic.sub_technique raw=%r normalized=%r category=%s family=%s",
            semantic.get("sub_technique", ""),
            sub_technique,
            taxonomy.category,
            family,
        )
    if taxonomy.category == "pwn":
        sub_technique = _normalize_pwn_semantic_sub_technique(family, sub_technique, allowed)
    return {"family": family, "sub_technique": sub_technique}


def _coerce_pwn_canonical_sub_technique(value: str) -> str:
    if value in PWN_TAXONOMY.semantic.fields["sub_technique"]:
        return value
    alias = PWN_CANONICAL_SUB_TECHNIQUE_ALIASES.get(value)
    if alias is not None:
        return alias
    return value


def _normalize_pwn_semantic_sub_technique(
    family: str,
    sub_technique: str,
    allowed: tuple[str, ...],
) -> str:
    if sub_technique in allowed:
        return sub_technique
    if family == "integer_oob" and any(
        token in sub_technique for token in ("bss", "global", "variable", "write")
    ):
        return "global_bss_write"
    return sub_technique


def _pwn_stack_supports_freeform(value: str) -> bool:
    return any(
        token in value
        for token in ("ret2", "overflow", "canary", "rop", "libc", "pivot")
    )


def _pwn_integer_oob_supports_freeform(value: str) -> bool:
    return any(token in value for token in ("bss", "global", "variable", "write", "oob", "overflow"))


def _first_eligible_candidate(
    *,
    category: str,
    taxonomy: CategoryProfileTaxonomy,
    policy: ProfilePolicy,
    semantic: Mapping[str, str],
    semantic_key: tuple[tuple[str, str], ...],
    planned: Sequence[ProfileCandidate],
    existing_signatures: set[str],
    existing_exact_keys: set[str],
    planned_signatures: set[str],
    planned_exact_keys: set[str],
    quota_caps: Mapping[str, int],
    quota_counts: Mapping[str, Counter[str]],
    quota_paths: Sequence[str],
    candidate_cache: dict[
        tuple[tuple[tuple[str, str], ...], int, int, int],
        _PreparedCandidate | None,
    ],
) -> ProfileCandidate | None:
    fallback_candidate: ProfileCandidate | None = None

    solve_rows = _compatible_solve_rows(category, semantic)
    implementation_rows = _compatible_implementation_rows(taxonomy, policy)
    presentation_rows = _axis_product(category, "presentation")
    for solve_index, solve in enumerate(solve_rows):
        for implementation_index, implementation in enumerate(implementation_rows):
            for presentation_index, presentation in enumerate(presentation_rows):
                cache_key = (
                    semantic_key,
                    solve_index,
                    implementation_index,
                    presentation_index,
                )
                if cache_key in candidate_cache:
                    prepared = candidate_cache[cache_key]
                else:
                    prepared = _prepare_candidate(
                        category=category,
                        policy=policy,
                        semantic=semantic,
                        solve=solve,
                        implementation=implementation,
                        presentation=presentation,
                        quota_paths=quota_paths,
                    )
                    candidate_cache[cache_key] = prepared
                if prepared is None:
                    continue
                candidate = prepared.candidate
                if candidate.signatures.combined_profile_signature in existing_signatures:
                    continue
                if candidate.signatures.combined_profile_signature in planned_signatures:
                    continue
                if candidate.exclusive_signature_key in existing_exact_keys:
                    continue
                if candidate.exclusive_signature_key in planned_exact_keys:
                    continue
                if _prepared_quota_exceeded(
                    prepared,
                    quota_caps=quota_caps,
                    quota_counts=quota_counts,
                ):
                    continue
                if not _cooldown_conflicts(candidate.profile, policy, planned):
                    return candidate
                if fallback_candidate is None:
                    fallback_candidate = candidate
    return fallback_candidate


def _prepare_candidate(
    *,
    category: str,
    policy: ProfilePolicy,
    semantic: Mapping[str, str],
    solve: Mapping[str, str],
    implementation: Mapping[str, str],
    presentation: Mapping[str, str],
    quota_paths: Sequence[str],
) -> _PreparedCandidate | None:
    profile = GovernedProfile(
        semantic=dict(semantic),
        solve=solve,
        implementation=implementation,
        presentation=presentation,
    )
    signatures = canonical_profile_signatures(
        profile,
        category=category,
        policy_version=policy.version,
    )
    if signatures.combined_profile_signature in policy.hard_forbidden_combined_signatures:
        return None
    return _PreparedCandidate(
        candidate=ProfileCandidate(
            profile=profile,
            signatures=signatures,
            occupancy_scope=category,
            exclusive_signature_key=_exclusive_key(
                profile,
                policy.hard_exclusive_signature,
            ),
        ),
        quota_values={
            path: _profile_value(profile, path)
            for path in quota_paths
        },
    )


def _cooldown_conflicts(
    profile: GovernedProfile,
    policy: ProfilePolicy,
    planned: Sequence[ProfileCandidate],
) -> bool:
    for path, window in policy.cooldowns.items():
        if window <= 0:
            continue
        recent = planned[-window:]
        value = _profile_value(profile, path)
        if any(_profile_value(item.profile, path) == value for item in recent):
            return True
    return False


def _quota_caps(
    taxonomy: CategoryProfileTaxonomy,
    policy: ProfilePolicy,
    target_count: int,
) -> dict[str, int]:
    caps: dict[str, int] = {}
    for path, ratio in policy.quota_ratios.items():
        value_count = len(_effective_values_for_path(taxonomy, policy, path))
        if value_count <= 1:
            continue
        configured_cap = max(1, _ceil(target_count * ratio))
        fair_share_cap = _ceil(target_count / value_count)
        caps[path] = max(configured_cap, fair_share_cap)
    return caps


def _prepared_quota_exceeded(
    candidate: _PreparedCandidate,
    *,
    quota_caps: Mapping[str, int],
    quota_counts: Mapping[str, Counter[str]],
) -> bool:
    for path, cap in quota_caps.items():
        value = candidate.quota_values[path]
        if quota_counts[path][value] + 1 > cap:
            return True
    return False


def _quota_exceeded(
    *,
    profile: GovernedProfile,
    policy: ProfilePolicy,
    planned: Sequence[ProfileCandidate],
    hard_existing: Sequence[ProfileOccupancy],
    target_count: int,
) -> bool:
    for path, ratio in policy.quota_ratios.items():
        if len(_effective_values_for_path(taxonomy_for_category(policy.category), policy, path)) <= 1:
            continue
        cap = max(1, _ceil(target_count * ratio))
        value = _profile_value(profile, path)
        count = sum(1 for item in planned if _profile_value(item.profile, path) == value)
        count += sum(1 for item in hard_existing if _profile_value(item.profile, path) == value)
        if count + 1 > cap:
            return True
    return False


def _effective_values_for_path(
    taxonomy: CategoryProfileTaxonomy,
    policy: ProfilePolicy,
    path: str,
) -> frozenset[str]:
    axis, field = _split_path(path)
    if axis == "implementation":
        rows = _compatible_implementation_rows(taxonomy, policy)
        return frozenset(row[field] for row in rows if field in row)
    return frozenset(_field_values(taxonomy, path))


def _compatible_implementation_rows(
    taxonomy: CategoryProfileTaxonomy,
    policy: ProfilePolicy,
) -> tuple[dict[str, str], ...]:
    rows = policy.compatibility.get("implementation")
    all_rows = _axis_product(taxonomy.category, "implementation")
    if not rows:
        return all_rows
    compatible: list[dict[str, str]] = []
    for implementation in all_rows:
        for row in rows:
            if all(
                implementation.get(field.removeprefix("implementation.")) == value
                for field, value in row.items()
            ):
                compatible.append(implementation)
                break
    return tuple(compatible)


def _compatible_solve_rows(
    category: str,
    semantic: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    rows = _axis_product(category, "solve")
    if category == "pwn" and semantic.get("sub_technique") == "global_bss_write":
        return tuple(
            row
            for row in rows
            if row.get("required_action") == "write_what_where"
            and row.get("chain_shape") == "global-write-win"
        )
    return rows


@lru_cache(maxsize=None)
def _axis_product(category: str, axis_name: ProfileAxis) -> tuple[dict[str, str], ...]:
    axis = taxonomy_for_category(category).axis(axis_name)
    items = list(axis.fields.items())
    rows: list[dict[str, str]] = [{}]
    for field, values in items:
        rows = [dict(row, **{field: value}) for row in rows for value in values]
    return tuple(rows)


def _validate_axis_values(
    taxonomy: CategoryProfileTaxonomy,
    axis_name: ProfileAxis,
    payload: Mapping[str, str],
) -> None:
    validate_profile(
        taxonomy,
        {
            "semantic": payload if axis_name == "semantic" else _first_axis_value(taxonomy.semantic),
            "solve": payload if axis_name == "solve" else _first_axis_value(taxonomy.solve),
            "implementation": payload
            if axis_name == "implementation"
            else _first_axis_value(taxonomy.implementation),
            "presentation": payload if axis_name == "presentation" else _first_axis_value(taxonomy.presentation),
        },
    )


def _first_axis_value(axis: AxisSchema) -> dict[str, str]:
    return {field: values[0] for field, values in axis.fields.items()}


def _normalize_semantic_value(value: str) -> str:
    return " ".join(
        value.strip().lower().replace("-", " ").replace("_", " ").split()
    )


def _normalize_family_value(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _semantic_slug(value: str) -> str:
    return "_".join(
        replaced
        for replaced in _NON_ALNUM_RE.sub("_", value.lower()).split("_")
        if replaced
    )


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _coerce_sub_technique(
    value: str,
    allowed: Sequence[str],
    *,
    family: str | None = None,
    category: str | None = None,
) -> str:
    aliases = dict(SUB_TECHNIQUE_ALIASES_BY_CATEGORY.get(category or "", {}))
    for category_aliases in SUB_TECHNIQUE_ALIASES_BY_CATEGORY.values():
        aliases.update(category_aliases)
    alias = aliases.get(value)
    normalized_allowed = {_normalize_semantic_value(item): item for item in allowed}
    if value in allowed:
        return value
    if value in normalized_allowed:
        return normalized_allowed[value]
    if alias is not None and alias in allowed:
        return alias
    if alias is not None and _normalize_semantic_value(alias) in normalized_allowed:
        return normalized_allowed[_normalize_semantic_value(alias)]
    if alias is not None:
        return alias
    if category == "pwn" and family == "format_string" and value:
        return _semantic_slug(value)
    for normalized, original in normalized_allowed.items():
        if normalized and (normalized in value.split() or normalized in value):
            return original
    if value:
        return value
    family_default = SUB_TECHNIQUE_FAMILY_DEFAULTS.get(str(family or ""))
    return family_default or value


def _profile_value(profile: GovernedProfile, path: str) -> str:
    axis, field = _split_path(path)
    return str(getattr(profile, axis)[field])


def _field_values(taxonomy: CategoryProfileTaxonomy, path: str) -> tuple[str, ...]:
    axis, field = _split_path(path)
    schema = taxonomy.axis(axis)
    try:
        return schema.fields[field]
    except KeyError as exc:
        raise ProfileTaxonomyError(f"unknown profile field path {path!r}") from exc


def _split_path(path: str) -> tuple[ProfileAxis, str]:
    parts = path.split(".", 1)
    if len(parts) != 2 or parts[0] not in PROFILE_AXES or not parts[1]:
        raise ProfileTaxonomyError(f"invalid profile field path {path!r}")
    return parts[0], parts[1]  # type: ignore[return-value]


def _exclusive_key(profile: GovernedProfile, paths: Sequence[str]) -> str:
    payload = {path: _profile_value(profile, path) for path in paths}
    return _digest(payload)


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _category_profile_raw(category: str, paths: ProjectPaths | None) -> Mapping[str, Any]:
    resolved = paths or ProjectPaths.discover()
    payload = read_json(resolved.generation_profile, {})
    if not isinstance(payload, Mapping):
        return {}
    profiles = payload.get("profiles")
    if isinstance(profiles, Mapping) and isinstance(profiles.get(category), Mapping):
        return profiles[category]
    categories = payload.get("categories")
    if isinstance(categories, Mapping) and isinstance(categories.get(category), Mapping):
        return categories[category]
    return {}


def _quota_ratios(raw: Any) -> Mapping[str, float]:
    values = dict(_DEFAULT_QUOTA_RATIOS)
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if 0.0 < parsed <= 1.0:
                values[str(key)] = parsed
    return values


def _cooldowns(raw: Any) -> Mapping[str, int]:
    values = dict(_DEFAULT_COOLDOWNS)
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            parsed = _nonnegative_int(value, -1)
            if parsed >= 0:
                values[str(key)] = parsed
    return values


def _compatibility(raw: Any) -> Mapping[str, tuple[Mapping[str, str], ...]]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, tuple[Mapping[str, str], ...]] = {}
    for group, rows in raw.items():
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            continue
        cleaned: list[Mapping[str, str]] = []
        for row in rows:
            if isinstance(row, Mapping):
                cleaned.append({str(key): str(value) for key, value in row.items()})
        if cleaned:
            result[str(group)] = tuple(cleaned)
    return result


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _ceil(value: float) -> int:
    parsed = int(value)
    return parsed if parsed == value else parsed + 1
