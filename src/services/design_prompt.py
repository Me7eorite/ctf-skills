"""Prompt assembly for structured challenge design attempts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.paths import ProjectPaths
from domain.design.difficulty import RUBRIC as DIFFICULTY_RUBRIC
from domain.design_tasks import DesignTask
from domain.research import GenerationRequest, ResearchFinding, ResearchSource

SHARED_GENERATION_STRATEGY = "shared_generation_strategy.md"

# Phase 1 (9-references → 3): the design skill is now a single core file
# plus a unified category-tactics catalog. cve-pivot.md is read on-demand by
# the agent, not injected into every design prompt. delivery-format moved to
# docs/delivery-formats/ and is no longer part of design.
# Phase 2 added difficulty-rubric.md so the agent sees the machine-checked
# tier thresholds (technique count, intended_path steps, novelty requirement).
ALWAYS_REFERENCE_FILES: tuple[str, ...] = (
    "design-core.md",
    "category-tactics.md",
    "difficulty-rubric.md",
    SHARED_GENERATION_STRATEGY,
)
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
    previous_error: str | None = None,
    previous_design_seed_path: str | None = None,
    prior_designs: Sequence[Mapping[str, Any]] = (),
    reservation: Mapping[str, Any] | None = None,
    ledger_snapshot: Mapping[str, Any] | None = None,
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
        "## Prior Batch Designs (plan AGAINST these — do not collapse into them)",
        _render_prior_designs(prior_designs),
        "## Build Budget",
        _render_build_budget(design_task.difficulty),
        "## Research Evidence",
        _render_findings(findings),
        "## Research Sources",
        _render_sources(sources),
        _render_governance_context(reservation, ledger_snapshot),
        _render_retry_seed(previous_design_seed_path),
        "## References",
        *(
            _render_reference(
                f"skills/design-challenges/references/{name}",
                context.references[name],
            )
            for name in reference_names
        ),
        _render_retry_feedback(previous_error),
        "## Output Contract",
        _render_output_contract(design_task, governed=reservation is not None),
        "## Pinned Values (copy verbatim into `challenges[0]`)",
        _render_pinned_values(design_task),
    ]
    return "\n\n".join(sections).rstrip() + "\n"


# Phase 4: the Output Contract used to be 25+ negative don't-rules. It
# is now a JSON Schema + 3 short invariants. The validator side
# (``domain.design.validator``) is the authoritative enforcement; the
# schema below is the agent-facing summary that mirrors it so the model
# can self-check before replying.
_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["event", "challenges"],
    "additionalProperties": False,
    "properties": {
        "event": {
            "type": "object",
            "required": ["flag_format"],
            "properties": {
                "name": {"type": "string"},
                "theme": {"type": "string"},
                "audience": {"type": "string"},
                "flag_format": {"type": "string"},
            },
        },
        "challenges": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "id", "title", "category", "difficulty", "points",
                    "deployment", "primary_technique", "learning_objective",
                    "prompt", "flag_location", "validation",
                    "artifacts", "hints", "intended_path",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "difficulty": {
                        "enum": ["easy", "medium", "hard", "expert"]
                    },
                    "points": {"type": "integer", "minimum": 1},
                    "deployment": {"type": "string"},
                    "port": {"type": ["integer", "null"]},
                    "language": {
                        "type": "string",
                        "description": (
                            "Required for re/pwn. For re, use the assigned "
                            "language/toolchain, e.g. c, cpp, rust, go, java, "
                            "or kotlin. For pwn, use c, cpp, rust, go, or asm "
                            "when assigned; do not default to c."
                        ),
                    },
                    "compiler": {
                        "type": "string",
                        "description": (
                            "Required for re/pwn. Examples: gcc, clang, g++, "
                            "rustc, go build, javac, kotlinc, "
                            "x86_64-w64-mingw32-gcc."
                        ),
                    },
                    "target_format": {
                        "type": "string",
                        "description": (
                            "Required for re/pwn. Examples: elf, exe, wasm, "
                            "jar, container."
                        ),
                    },
                    "target_platform": {
                        "type": "string",
                        "description": (
                            "Required for re unless target_format is jar/wasm; "
                            "examples: linux/amd64, linux/arm64, windows/amd64."
                        ),
                    },
                    "techniques": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "primary_technique": {"type": "string", "minLength": 1},
                    "secondary_technique": {"type": "string"},
                    "learning_objective": {"type": "string", "minLength": 1},
                    "prompt": {"type": "string", "minLength": 1},
                    "difficulty_reason": {
                        "type": "string",
                        "description": (
                            "Required for medium/hard/expert. Explain why the "
                            "declared asset/capability chain truly matches the "
                            "claimed difficulty."
                        ),
                    },
                    "flag_location": {"type": "string", "minLength": 1},
                    "flag_plan": {
                        "type": "object",
                        "properties": {
                            "format": {"type": "string"},
                            "location": {"type": "string"},
                            "generation": {"type": "string"},
                        },
                    },
                    "intended_path": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "unintended_solutions": {
                        "type": "array",
                        "description": (
                            "Required for medium/hard/expert (a single intended "
                            "solve path). Each entry names one alternate/unintended "
                            "solution you considered and how the design blocks it. "
                            "easy MAY omit this and allow multiple solve paths."
                        ),
                        "items": {"type": "string", "minLength": 1},
                    },
                    "actual_solution_type": {
                        "type": "array",
                        "description": (
                            "Required for medium/hard/expert. The real solve "
                            "type(s) — must exercise the nominal technique and "
                            "MUST NOT be a generic collapse shortcut (e.g. "
                            "static_xor_decrypt/direct_run_get_flag for re, "
                            "default_credentials/exposed_flag_route for web, "
                            "unintended_win_function/direct_shellcode for pwn). "
                            "easy MAY omit it."
                        ),
                        "items": {"type": "string", "minLength": 1},
                    },
                    "asset_flow": {
                        "type": "array",
                        "description": (
                            "The required asset/capability chain. Each stage must "
                            "produce something the next stage needs — this is what "
                            "makes a challenge medium+ rather than a pile of "
                            "techniques. Required for medium (>=1 transition) and "
                            "hard (>=2 transitions); easy MAY omit it or use a "
                            "direct flow. A transition counts only when the stage "
                            "has both produced_asset_or_capability and "
                            "why_next_stage_requires_it."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "stage": {"type": "integer"},
                                "player_input_or_capability": {"type": "string"},
                                "technique": {"type": "string"},
                                "produced_asset_or_capability": {"type": "string"},
                                "why_next_stage_requires_it": {"type": "string"},
                            },
                        },
                    },
                    "shortcut_closure": {
                        "type": "array",
                        "description": (
                            "Required for medium/hard/expert. Each entry names a "
                            "shortcut class considered and how the design blocks "
                            "it: direct flag access, client-side gates, guessable "
                            "tokens/URLs/IDs/seeds, public flag exposure, solver "
                            "bypass, or similar collapse paths."
                        ),
                        "items": {"type": "string", "minLength": 1},
                    },
                    "fingerprint": {
                        "type": "object",
                        "description": (
                            "Required for medium/hard/expert. Shape-level "
                            "fingerprint used for later duplicate analysis."
                        ),
                        "properties": {
                            "entrypoint_type": {"type": "string"},
                            "asset_flow_shape": {"type": "string"},
                            "flag_access_model": {"type": "string"},
                            "scenario_type": {"type": "string"},
                        },
                    },
                    "artifacts": {
                        "type": "array",
                        "minItems": 5,
                        "items": {
                            "type": "string",
                            "description": (
                                "A safe challenge-relative file path. Native "
                                "executables and Makefiles may be extensionless. "
                            "For pwn with runtime_profile=xinetd or an "
                            "xinetd/chroot service_model, include "
                            "deploy/_files/ctf.xinetd and set the template "
                            "or implementation_plan scaffold to "
                            "pwn/xinetd-chroot."
                            ),
                            "pattern": (
                                r"^(?:README\.md|metadata\.json|validate\.sh|"
                                r"(?:deploy|writenup|attachments|dist|src)/"
                                r"(?!\.\.(?:/|$))(?!.*\/\.\.(?:/|$))"
                                r"[^\r\n\t/]+(?:/[^\r\n\t/]+)*)$"
                            ),
                        },
                    },
                    "validation": {"type": "string", "minLength": 1},
                    "hints": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "implementation_plan": {
                        "type": "object",
                        "description": (
                            "Intent-level only. NO Dockerfile bodies, NO "
                            "compose YAML, NO SQL scripts, NO exploit code, "
                            "NO file contents. Component cap depends on "
                            "difficulty (see Build Budget section)."
                        ),
                        "properties": {
                            "runtime": {
                                "type": "string",
                                "description": (
                                    "Required for web. Use the assigned stack "
                                    "exactly, e.g. python:3.11-slim, node:20, "
                                    "php:8-apache, java:17-tomcat, golang, "
                                    "or rust."
                                ),
                            },
                            "framework": {
                                "type": "string",
                                "description": (
                                    "Required for web. Use the assigned framework "
                                    "exactly, e.g. Flask, Express, plain PHP, "
                                    "Spring Boot, Jakarta Servlet, Gin, Axum, "
                                    "Actix Web, or Rocket."
                                ),
                            },
                            "runtime_language": {
                                "type": "string",
                                "description": (
                                    "Required for web runtime selection; also "
                                    "accepted for pwn native services. Web values: "
                                    "python, node, php, java, go, rust. Pwn values: "
                                    "c, cpp, rust, go, asm or pwn/native. Do not "
                                    "default to python."
                                ),
                            },
                            "runtime_profile": {
                                "type": "string",
                                "description": (
                                    "Optional runtime profile such as default, "
                                    "jar, tomcat, binary, kernel, or xinetd."
                                ),
                            },
                            "service_user": {
                                "type": "string",
                                "description": (
                                    "Required for node/go/rust/java jar and pwn "
                                    "default/native/binary/xinetd/chroot/kernel "
                                    "services. For pwn default/native/binary/"
                                    "xinetd/chroot/kernel, set exactly `ctf`; "
                                    "this field is the challenge service process "
                                    "user, not the xinetd daemon user. PHP may use `www-data`, "
                                    "`apache`, or `ctf`; Tomcat uses `tomcat`."
                                ),
                            },
                            "components": {
                                "type": "array",
                                "description": (
                                    "Optional names of independently buildable "
                                    "or deployable components. Do not list metadata "
                                    "fields such as runtime, entrypoints, or flag handling."
                                ),
                                "items": {"type": "string", "minLength": 1},
                            }
                        },
                    },
                    "novelty": {
                        "type": "string",
                        "description": (
                            "Required for `expert`. ≥ 40 chars. Identifies "
                            "the 0day-style trick or unusual constraint."
                        ),
                    },
                },
            },
        },
    },
}


def _render_output_contract(task: DesignTask, *, governed: bool = False) -> str:
    """Render the JSON Schema + 3 short invariants.

    Phase 4: the old 25-line negative-list got modelled into the schema
    above so the agent can self-validate against one block instead of
    scanning a wall of prose rules.
    """
    schema = _governed_output_schema() if governed else _OUTPUT_SCHEMA
    schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
    container_artifacts_hint = ""
    if task.category == "web":
        container_artifacts_hint = (
            "\n- For web, `artifacts` must additionally include "
            "`deploy/Dockerfile`, `deploy/docker-compose.yml`, "
            "`deploy/_files/start.sh`, and runtime-specific source files. "
            "Examples: Python `deploy/src/app.py`; Node `deploy/src/package.json` "
            "plus `deploy/src/server.js`/`app.js`/`index.js`; PHP "
            "`deploy/src/index.php`; Go `deploy/src/main.go`; Java jar "
            "`deploy/src/Main.java` or `deploy/src/src/main/java/...` plus a "
            "build file; Tomcat `deploy/src/src/main/webapp/WEB-INF/web.xml` "
            "plus a Servlet/JSP; Rust `deploy/src/Cargo.toml` plus "
            "`deploy/src/src/main.rs` or `deploy/src/main.rs`. Declare "
            "`implementation_plan.service_user`: Node/Go/Rust/Java jar use "
            "`ctf`; PHP may use `www-data`, `apache`, or `ctf`; Tomcat uses "
            "`tomcat`."
        )
    elif task.category == "pwn":
        container_artifacts_hint = (
            "\n- For pwn, `artifacts` must additionally include "
            "`deploy/Dockerfile`, `deploy/docker-compose.yml`, "
            "`deploy/_files/start.sh`, `deploy/_files/ctf.xinetd`, and "
            "native binary service artifacts. "
            "Use diverse challenge-specific names under `deploy/src/` or "
            "`src/`. A small multi-file project is valid: list every planned "
            "source/build artifact such as `deploy/src/src/main.c`, "
            "`deploy/src/lib/menu.c`, `deploy/src/include/menu.h`, "
            "`deploy/src/Makefile`, or `deploy/src/bin/challenge`; it is not "
            "limited to a single `deploy/src/vuln.c`. Ordinary pwn tasks MUST "
            "use the xinetd/chroot service model, set "
            "`implementation_plan.runtime_profile` to `xinetd`, declare the "
            "deployment template/scaffold as `pwn/xinetd-chroot`, and set "
            "`implementation_plan.service_user` to exactly `ctf`. This "
            "`service_user` is the challenge service process user inside the "
            "chroot; xinetd itself may start as root to bind/dispatch, but "
            "that daemon identity is not what this field records. Validation "
            "will reject ordinary pwn designs that try `root` or `xinetd` as "
            "the service user. `deploy/_files/ctf.xinetd` is REQUIRED in "
            "`artifacts` or validation will reject the design with "
            "`runtime (pwn/xinetd) artifact requires at least one of: "
            "deploy/_files/ctf.xinetd, deploy/_files/etc/xinetd.d/ctf, "
            "deploy/_files/etc/xinetd.d/chal`. Also declare the deployment "
            "template/scaffold as `pwn/xinetd-chroot`, which maps to "
            "`scaffolds/pwn/xinetd-chroot/` in the build stage. Do not include "
            "Python `deploy/src/app.py` unless the pwn service is intentionally "
            "a Python wrapper around a separate native binary."
        )
    uniqueness_hint = (
        "\n5. This is a `" + task.difficulty + "` challenge: it MUST have a "
        "SINGLE intended solve path. Populate `unintended_solutions` with a "
        "non-empty list — each entry naming one alternate/unintended solution "
        "you considered and exactly how the design blocks it (mitigation, "
        "constraint, or removed primitive)."
        if task.difficulty != "easy"
        else "\n5. This is an `easy` challenge: multiple solve paths are "
        "acceptable; `unintended_solutions` is optional."
    )
    _asset_min = {"medium": 1, "hard": 2, "expert": 1}.get(task.difficulty, 0)
    asset_flow_hint = (
        f"\n6. This `{task.difficulty}` challenge MUST encode a required "
        f"asset/capability chain in `asset_flow` with at least {_asset_min} "
        "effective transition(s): each such stage produces a concrete "
        "`produced_asset_or_capability` that the next stage cannot proceed "
        "without (`why_next_stage_requires_it`). Techniques that do not feed "
        "the next stage do not count; generic assets like `access`, `data`, "
        "`result`, or `permission` do not count. The flag must not be reachable "
        "while skipping the chain. Also populate `difficulty_reason`, "
        "`shortcut_closure`, and `fingerprint`."
        if _asset_min > 0
        else "\n6. This `easy` challenge MAY omit `asset_flow` or use a direct "
        "observe→exploit→flag flow; no required chain is enforced."
    )
    solution_type_hint = (
        "\n7. Declare a non-empty `actual_solution_type` that exercises the "
        "nominal technique. It MUST NOT be a generic collapse shortcut for this "
        "category (e.g. static_xor_decrypt / direct_run_get_flag for re, "
        "default_credentials / exposed_flag_route for web, "
        "unintended_win_function / direct_shellcode for pwn)."
        if task.difficulty != "easy"
        else "\n7. `actual_solution_type` is optional for `easy`."
    )
    governance_hint = (
        "\n8. This task has a governed profile reservation. Copy the supplied "
        "`reserved_profile` exactly into `challenges[0].governed_profile`; "
        "do not choose alternate governed values. Provide "
        "`design_evidence`, `distinctness_claim`, `compared_challenge_ids`, "
        "and `build_contract`. The build contract is authoritative for Build: "
        "use only symbolic artifact/fixture ids and closed harness kinds, never "
        "shell commands, argv, executable paths, or file contents."
        if governed
        else ""
    )
    invariants = (
        "Invariants (enforced server-side; violating any of these fails "
        "the attempt):\n"
        "1. Your final answer MUST be a SINGLE JSON object matching the "
        "schema below — no markdown, code fences, prose, or secondary "
        "artifacts. Prefer direct stdout JSON. If you also write a file for "
        "recovery, write ONLY `./state/design_output.json` with the same "
        "JSON object. Do not read or depend on any output file that the host "
        "did not provide; previous retry seed files, when present, are named "
        "`./state/previous_design.json`. Any filesystem output under the "
        "project root fails the attempt.\n"
        "2. `artifacts` MUST be relative local paths and MUST include "
        "`README.md`, `metadata.json`, `validate.sh`, `writenup/wp.md`, "
        "and `writenup/exp.py`. Extensionless native executables and "
        "conventional build files are valid; for example "
        "`attachments/crackme` and `deploy/Makefile`."
        " `challenges[0].title` MUST be copied exactly from Pinned Values; "
        "do not invent titles like `<topic> task <n>`."
        " Do not include generated scaffold paths such as "
        "`output/challenges/...`, `src/output/...`, or "
        "`attachments/output/...`; all required evidence files live directly "
        "under the canonical challenge root."
        + container_artifacts_hint
        + "\n3. `validation` MAY reference local compose URLs "
        "(`http://127.0.0.1:<port>`, `http://localhost:<port>`) but MUST "
        "NOT require external HTTP/HTTPS URLs, and MUST NOT contain code "
        "or file bodies."
        + "\n4. For `category = re`, do not make the delivered artifact "
        "trivially reveal `metadata.flag` via `strings` unless "
        "`primary_technique` explicitly says the intended solve is "
        "`strings on the binary`; likewise, `validate.sh` and "
        "`writenup/exp.py` MUST NOT embed the literal `metadata.flag`."
        + uniqueness_hint
        + asset_flow_hint
        + solution_type_hint
        + governance_hint
    )
    return f"{invariants}\n\n```json\n{schema_text}\n```"


def _governed_output_schema() -> dict[str, Any]:
    schema = json.loads(json.dumps(_OUTPUT_SCHEMA))
    challenge_schema = schema["properties"]["challenges"]["items"]
    challenge_schema["required"] = [
        *challenge_schema["required"],
        "governed_profile",
        "design_evidence",
        "distinctness_claim",
        "compared_challenge_ids",
        "build_contract",
    ]
    properties = challenge_schema["properties"]
    properties["governed_profile"] = {"type": "object"}
    properties["design_evidence"] = {
        "type": "object",
        "required": ["research_finding_ids", "claims"],
        "properties": {
            "research_finding_ids": {
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
                "minItems": 1,
            },
            "claims": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
            },
            "notes": {"type": "string"},
        },
    }
    properties["distinctness_claim"] = {"type": "string", "minLength": 1}
    properties["compared_challenge_ids"] = {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
    }
    properties["build_contract"] = {
        "type": "object",
        "required": [
            "required_profile",
            "required_player_actions",
            "required_components",
            "required_asset_flow",
            "forbidden_shortcuts",
            "acceptance_tests",
            "allowed_implementation_freedom",
        ],
            "properties": {
                "artifact_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
            "fixture_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "required_profile": {"type": "object"},
            "required_player_actions": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
            },
            "required_components": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "required_asset_flow": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/buildContractAssetStage"},
            },
            "forbidden_shortcuts": {
                "type": "array",
                "description": (
                    "Array of harness objects. Never emit string entries such as "
                    "[\"no direct flag read\"]."
                ),
                "items": {"$ref": "#/$defs/buildContractHarness"},
            },
            "acceptance_tests": {
                "type": "array",
                "description": (
                    "Array of harness objects. Never emit string entries such as "
                    "[\"must pass solve\"]."
                ),
                "items": {"$ref": "#/$defs/buildContractHarness"},
            },
            "allowed_implementation_freedom": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        },
    }
    schema.setdefault("$defs", {}).update(
        {
            "buildContractHarness": {
                "type": "object",
                "required": ["test_kind", "assertion"],
                "properties": {
                    "id": {"type": "string"},
                    "test_kind": {
                        "enum": [
                            "artifact_direct_run",
                            "fixture_assertion",
                            "solver_with_fixture",
                            "solver_without_fixture",
                            "random_flag_rebuild",
                        ]
                    },
                    "assertion": {
                        "enum": [
                            "stdout_not_contains_flag",
                            "must_fail",
                            "non_empty",
                            "equals",
                            "contains",
                            "must_pass",
                            "outputs_flag",
                            "outputs_new_flag",
                            "old_flag_rejected",
                        ]
                    },
                    "artifact_ref": {"type": "string"},
                    "fixture_ref": {"type": "string"},
                    "input_fixture": {"type": "string"},
                },
                "not": {
                    "anyOf": [
                        {"required": ["command"]},
                        {"required": ["argv"]},
                        {"required": ["shell"]},
                        {"required": ["path"]},
                        {"required": ["cwd"]},
                        {"required": ["executable"]},
                    ]
                },
                "additionalProperties": True,
            },
            "buildContractAssetStage": {
                "type": "object",
                "required": [
                    "stage_id",
                    "produced_asset_or_capability",
                    "verification_harness",
                    "dependency_harness",
                ],
                "properties": {
                    "stage_id": {"type": "string", "minLength": 1},
                    "produced_asset_or_capability": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "verification_harness": {
                        "$ref": "#/$defs/buildContractHarness"
                    },
                    "dependency_harness": {
                        "$ref": "#/$defs/buildContractHarness"
                    },
                },
                "additionalProperties": False,
            },
        }
    )
    return schema


def _render_governance_context(
    reservation: Mapping[str, Any] | None,
    ledger_snapshot: Mapping[str, Any] | None,
) -> str:
    if reservation is None:
        return ""
    reserved_profile = reservation.get("reserved_profile")
    required_action = None
    if isinstance(reserved_profile, Mapping):
        solve = reserved_profile.get("solve")
        if isinstance(solve, Mapping):
            value = solve.get("required_action")
            if isinstance(value, str) and value.strip():
                required_action = value.strip()
    return "\n".join(
        [
            "## Governed Design Reservation",
            "This is authoritative server-provided governance context. Copy "
            "`reserved_profile` exactly into `challenges[0].governed_profile` "
            "and into `challenges[0].build_contract.required_profile`.",
            "",
            _render_governed_contract_rules(
                required_action=required_action,
                ledger_snapshot=ledger_snapshot,
            ),
            "",
            "```json",
            json.dumps(
                {
                    "reservation": dict(reservation),
                    "ledger_snapshot": dict(ledger_snapshot or {}),
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            "```",
        ]
    )


def _render_governed_contract_rules(
    *,
    required_action: str | None,
    ledger_snapshot: Mapping[str, Any] | None,
) -> str:
    compared_ids = _ledger_compared_ids(ledger_snapshot)
    action_hint = (
        f"`build_contract.required_player_actions` MUST include exactly "
        f"`{required_action}` from `reserved_profile.solve.required_action`."
        if required_action
        else "`build_contract.required_player_actions` MUST include the exact "
        "value from `reserved_profile.solve.required_action`."
    )
    compared_hint = (
        "`compared_challenge_ids` may be empty, or may only contain these "
        f"ledger ids: {', '.join(compared_ids)}."
        if compared_ids
        else "`compared_challenge_ids` MUST be [] because this ledger snapshot "
        "does not supply comparable challenge ids."
    )
    return "\n".join(
        [
            "Governance fields are validated more strictly than the JSON Schema:",
            f"- {action_hint}",
            "- `governed_profile` MUST exactly match `reserved_profile`.",
            "- `build_contract.required_profile` MUST exactly match `governed_profile`.",
            "- `build_contract.required_player_actions` MUST include the "
            "reserved solve action exactly as declared in `reserved_profile`.",
            "- `distinctness_claim` must explain both solve-axis differences "
            "and implementation-axis differences; mentioning the reserved solve "
            "values and implementation values is valid.",
            f"- {compared_hint}",
            "- `build_contract.required_asset_flow` must be a non-empty array "
            "of objects with unique `stage_id` values. Every stage needs "
            "`produced_asset_or_capability`, `verification_harness`, and "
            "`dependency_harness`.",
            "- Declare symbolic `artifact_ids` and `fixture_ids` before any "
            "harness references them. Harnesses cannot contain `command`, "
            "`argv`, `shell`, `path`, `cwd`, or `executable`.",
            "- `build_contract.forbidden_shortcuts` and "
            "`build_contract.acceptance_tests` must be arrays of harness "
            "objects, never strings. If there is no concrete harness to add, "
            "use `[]` rather than a string placeholder.",
            "- Closed harness kinds/assertions: "
            "`artifact_direct_run` -> `stdout_not_contains_flag` or `must_fail`; "
            "`fixture_assertion` -> `non_empty`, `equals`, or `contains`; "
            "`solver_with_fixture` -> `must_pass` or `outputs_flag`; "
            "`solver_without_fixture` -> `must_fail` or `stdout_not_contains_flag`; "
            "`random_flag_rebuild` -> `outputs_new_flag` or `old_flag_rejected` "
            "(re only).",
            "- Harness references must point at declared `artifact_ids`, "
            "`fixture_ids`, or `input_fixture` ids; if there is nothing concrete "
            "to reference, leave the field out.",
        ]
    )


def _ledger_compared_ids(
    ledger_snapshot: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if not isinstance(ledger_snapshot, Mapping):
        return ()
    ids: list[str] = []
    for key in ("sibling_entries", "historical_entries"):
        entries = ledger_snapshot.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            challenge_id = entry.get("challenge_id")
            if isinstance(challenge_id, str) and challenge_id.strip():
                ids.append(challenge_id.strip())
    return tuple(dict.fromkeys(ids))


def _render_retry_feedback(previous_error: str | None) -> str:
    """Tell a retry what the preceding attempt must correct."""
    if not previous_error:
        return ""
    concise_error = previous_error.strip()[:1000]
    return "\n".join(
        [
            "## Retry Feedback",
            "The preceding attempt failed server-side validation. Correct this "
            "specific problem before replying:",
            "",
            f"- {concise_error}",
            "- Re-check the complete Output Contract after making the correction.",
        ]
    )


def _render_retry_seed(previous_design_seed_path: str | None) -> str:
    if not previous_design_seed_path:
        return ""
    return "\n".join(
        [
            "## Previous Draft Seed",
            f"A prior draft JSON has been staged at `{previous_design_seed_path}`.",
            "Use it as the base object for this retry and change only the fields "
            "needed to satisfy the latest error.",
        ]
    )


def _render_build_budget(difficulty: str) -> str:
    """Quote the per-tier buildability caps so the agent self-constrains.

    Phase 2.5 (D5=a): timeouts stay category-based (set in core/build_timeout);
    this block keeps the design within the scope the build phase can actually
    finish before hitting them.
    """
    rubric = DIFFICULTY_RUBRIC.get(difficulty)
    if rubric is None:
        return "(unknown difficulty — no budget enforced)"
    return "\n".join(
        [
            f"Buildability budget for `{difficulty}` (enforced by validator + "
            "consumed by the build agent):",
            "",
            f"- techniques: {_range_text(rubric.techniques_min, rubric.techniques_max)}",
            f"- intended_path steps: ≤ {rubric.intended_path_max}",
            f"- explicit `implementation_plan.components` entries: ≤ "
            f"{rubric.implementation_component_max}",
            f"- estimated total build LOC (guidance, not enforced): ≤ "
            f"{rubric.estimated_loc_budget}",
            f"- business scenario required: "
            f"{'yes' if rubric.needs_business_scenario else 'no'}",
            f"- implementation_plan required: "
            f"{'yes' if rubric.needs_implementation_plan else 'no'}",
            f"- novelty field required: "
            f"{'yes' if rubric.needs_novelty else 'no'}",
            f"- single intended solve path (unintended_solutions required): "
            f"{'yes' if rubric.needs_unique_solution else 'no — multiple paths allowed'}",
            f"- required asset_flow transitions: "
            f"{rubric.min_asset_transitions if rubric.min_asset_transitions else 'none (direct flow allowed)'}",
            "",
            "If your design cannot fit this budget, simplify or split it; "
            "otherwise upgrade the difficulty tier.",
        ]
    )


def _range_text(low: int, high: int) -> str:
    if low == high:
        return f"exactly {low}"
    if high >= 99:
        return f"≥ {low}"
    return f"{low}–{high}"


def _render_pinned_values(task: DesignTask) -> str:
    # Hard-coded copies of the fields the validator compares for equality
    # against the parent design task. These are the exact strings/numbers
    # the agent must echo into `challenges[0]`; any drift fails the attempt.
    lines = [
        "These values are validated by exact match against the database.",
        "Any drift (even cosmetic) fails the attempt.",
        "",
        f"- `challenges[0].id` = `{task.challenge_id}`",
        f"- `challenges[0].title` = `{task.title}` (copy exactly; <=15 ASCII letters; no `task` wording)",
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
    lines = [
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
    vocabulary = _advisory_mechanism_vocabulary(task)
    if vocabulary:
        lines.append(
            "- advisory_mechanism_vocabulary: "
            + json.dumps(vocabulary, ensure_ascii=False, sort_keys=True)
            + " — examples only; choose the mechanism from the request, "
            "research evidence, and coherent design."
        )
    lines.extend(
        [
            "- chosen_mechanism: MUST be declared by the design model, not pre-assigned by code.",
            "- semantic_fingerprint: MUST summarize the final mechanism/asset flow in one stable phrase.",
            "- diversity_rationale: MUST explain why this design differs from "
            "sibling tasks without relying on a forced template.",
        ]
    )
    return "\n".join(lines)


def _advisory_mechanism_vocabulary(task: DesignTask) -> list[str]:
    flags = getattr(task, "diversity_flags", None)
    if isinstance(flags, Mapping):
        value = flags.get("advisory_mechanism_vocabulary")
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item).strip()]
    return []


def _render_prior_designs(prior_designs: Sequence[Mapping[str, Any]]) -> str:
    """Render digests of already-designed sibling tasks for anti-collapse planning."""
    if not prior_designs:
        return (
            "- (none yet — this is the first design in the batch)\n"
            "First analyze how a strong batch should spread across distinct "
            "concepts, mechanisms, and solution shapes, then plan this one."
        )
    lines = [
        "These tasks in the SAME batch are already designed. Your design MUST be "
        "meaningfully different: do NOT reuse the same primary technique, the "
        "same flag-protection / core mechanism, or the same asset-flow shape. "
        "If your natural plan collapses toward one of these, change the "
        "mechanism or the required path so the batch stays diverse.",
        "",
    ]
    for d in prior_designs:
        flow = " -> ".join(d.get("asset_flow_shape") or []) or "(direct)"
        techs = ", ".join(d.get("techniques") or []) or d.get("primary_technique") or "?"
        lines.append(
            f"- `{d.get('id')}` [{d.get('category')}/{d.get('difficulty')}] "
            f"primary={d.get('primary_technique')!r}; techniques={techs}; "
            f"asset_flow={flow}"
        )
    return "\n".join(lines)


def _render_findings(findings: Sequence[ResearchFinding]) -> str:
    capped = list(findings[:EVIDENCE_FINDING_LIMIT])
    if not capped:
        return "- (no cited research findings)"
    lines: list[str] = [
        "For governed designs, `design_evidence.research_finding_ids` MUST "
        "copy UUIDs from this list only. Do not invent, shorten, or cite "
        "source IDs.",
        "",
    ]
    for index, finding in enumerate(capped, start=1):
        lines.append(
            f"- {index}. id={finding.id} [{finding.kind}] "
            f"{finding.label}: {finding.summary}"
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
