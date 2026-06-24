"""Difficulty-aware content alignment for validated design payloads.

Phase 2 of the design-skill rework: the structural validator (see
:mod:`domain.design.validator`) only enforces field shapes. Difficulty
alignment is checked here so the rules stay readable and so the per-tier
table is the single source of truth.

The tier table is the validator source of truth. Keep
``skills/design-challenges/references/difficulty-rubric.md`` and the rendered
build-budget prompt synchronized with it when updating prose guidance.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from domain.design.mechanical_transforms import is_mechanical_transform
from domain.design.schema import ChallengeDesignValidationError
from domain.design.technique_taxonomy import resolve_sub_technique
from domain.design_tasks import DesignTask

_LOGGER = logging.getLogger(__name__)

# Minimum length for the player prompt at medium and above. A one-liner
# is too thin to carry business context.
MIN_BUSINESS_PROMPT_CHARS = 60

# Expert ``novelty`` must be substantive enough to identify the trick.
MIN_NOVELTY_CHARS = 40
MIN_DIFFICULTY_REASON_CHARS = 30

_GENERIC_ASSET_WORDS: frozenset[str] = frozenset(
    {
        "access",
        "data",
        "info",
        "information",
        "knowledge",
        "permission",
        "permissions",
        "privilege",
        "privileges",
        "result",
        "state",
        "thing",
        "value",
    }
)

_GENERIC_DEPENDENCY_PHRASES: frozenset[str] = frozenset(
    {
        "needed for next step",
        "needed to continue",
        "required for next step",
        "required to continue",
        "used later",
    }
)

# Enforcement modes for the difficulty rubric. Default is ``strict`` —
# any violation raises ChallengeDesignValidationError. ``lenient`` logs
# each violation and lets the design through; that mode exists so
# GLM-5 / DeepSeek-class models that are less consistent at hitting
# the rubric do not waste an entire design attempt over a single edge.
# Set ``DESIGN_DIFFICULTY_ENFORCEMENT=lenient`` in the runtime env to
# opt in per-deployment.
_ENFORCEMENT_STRICT = "strict"
_ENFORCEMENT_LENIENT = "lenient"


def _enforcement_mode() -> str:
    raw = os.environ.get("DESIGN_DIFFICULTY_ENFORCEMENT", _ENFORCEMENT_STRICT)
    mode = raw.strip().lower() if isinstance(raw, str) else _ENFORCEMENT_STRICT
    if mode not in {_ENFORCEMENT_STRICT, _ENFORCEMENT_LENIENT}:
        _LOGGER.warning(
            "invalid DESIGN_DIFFICULTY_ENFORCEMENT=%r; falling back to strict",
            raw,
        )
        return _ENFORCEMENT_STRICT
    return mode


@dataclass(frozen=True)
class DifficultyRubric:
    """Machine-checked thresholds for one difficulty tier.

    ``implementation_component_max`` caps the explicit entries in
    ``implementation_plan.components`` to keep build-phase scope under control.
    Descriptive fields such as ``runtime`` and ``flag_handling`` are metadata,
    not independently buildable components.
    ``estimated_loc_budget`` is rendered into the design prompt as
    guidance (no validator-side check, since the design has no code
    yet — the build agent uses it to self-trim).
    """

    techniques_min: int
    techniques_max: int  # inclusive; use a large sentinel for "no upper bound"
    intended_path_min: int
    intended_path_max: int
    needs_business_scenario: bool
    needs_implementation_plan: bool
    needs_novelty: bool
    implementation_component_max: int
    estimated_loc_budget: int
    # 解题唯一性：easy 允许多解；medium 及以上要求单一预期解，
    # 并强制作者在 ``unintended_solutions`` 中枚举已堵掉的替代解。
    needs_unique_solution: bool = False
    # 资产流：medium 及以上要求一条"必须经过"的资产/能力链。这里是有效转移的
    # 下限——每个有效转移 = 某阶段产出非空资产/能力且被下一阶段明确依赖。
    # easy=0（允许直链），medium=1，hard=2，expert=1（expert 以 novelty 为主）。
    min_asset_transitions: int = 0
    # 声明的实际解法类型：medium 及以上必须声明 actual_solution_type，且不得是
    # 该类别的"万能捷径"（否则名义考点沦为装饰，解法已坍缩）。
    needs_actual_solution_type: bool = False


RUBRIC: dict[str, DifficultyRubric] = {
    "easy": DifficultyRubric(
        techniques_min=1,
        techniques_max=1,
        intended_path_min=1,
        intended_path_max=4,
        needs_business_scenario=False,
        needs_implementation_plan=False,
        needs_novelty=False,
        implementation_component_max=5,
        estimated_loc_budget=200,
        needs_unique_solution=False,
        min_asset_transitions=0,
        needs_actual_solution_type=False,
    ),
    "medium": DifficultyRubric(
        techniques_min=2,
        techniques_max=3,
        intended_path_min=1,
        intended_path_max=5,
        needs_business_scenario=True,
        needs_implementation_plan=False,
        needs_novelty=False,
        implementation_component_max=7,
        estimated_loc_budget=400,
        needs_unique_solution=True,
        min_asset_transitions=1,
        needs_actual_solution_type=True,
    ),
    "hard": DifficultyRubric(
        techniques_min=3,
        techniques_max=4,
        intended_path_min=1,
        intended_path_max=7,
        needs_business_scenario=True,
        needs_implementation_plan=True,
        needs_novelty=False,
        implementation_component_max=10,
        estimated_loc_budget=700,
        needs_unique_solution=True,
        min_asset_transitions=2,
        needs_actual_solution_type=True,
    ),
    "expert": DifficultyRubric(
        techniques_min=2,
        techniques_max=99,
        intended_path_min=1,
        intended_path_max=10,
        needs_business_scenario=True,
        needs_implementation_plan=True,
        needs_novelty=True,
        implementation_component_max=15,
        estimated_loc_budget=1200,
        needs_unique_solution=True,
        min_asset_transitions=1,
        needs_actual_solution_type=True,
    ),
}


def validate_difficulty_alignment(
    challenge: Mapping[str, Any],
    parent_task: DesignTask,
    *,
    legacy_grandfather: bool = False,
) -> None:
    """Reject ``challenge`` if its content does not match its difficulty tier.

    Set ``legacy_grandfather=True`` for designs created before this
    validator existed; the function returns immediately in that case so
    historical rows do not have to be regenerated.

    Set ``DESIGN_DIFFICULTY_ENFORCEMENT=lenient`` in the runtime env to
    log violations instead of raising. Useful when the model is GLM-5 /
    DeepSeek-class and operators want the design through with a warning.
    """
    if legacy_grandfather:
        return

    difficulty = parent_task.difficulty
    rubric = RUBRIC.get(difficulty)
    if rubric is None:
        # Structural validator already enforces ``difficulty`` is canonical;
        # this branch only fires if RUBRIC and DIFFICULTY_LABELS drift apart.
        raise ChallengeDesignValidationError(
            f"no difficulty rubric defined for {difficulty!r}"
        )

    enforcement = _enforcement_mode()
    violations: list[str] = []

    def _flag(message: str) -> None:
        violations.append(message)

    technique_count = _count_techniques(challenge)
    if technique_count < rubric.techniques_min:
        _flag(
            f"{difficulty} requires at least {rubric.techniques_min} distinct "
            f"techniques; design only declares {technique_count}"
        )
    if technique_count > rubric.techniques_max:
        _flag(
            f"{difficulty} allows at most {rubric.techniques_max} distinct "
            f"techniques; design declares {technique_count}. Simplify the design "
            "or upgrade the difficulty tier."
        )

    step_count = _count_intended_path_steps(challenge)
    if step_count > rubric.intended_path_max:
        _flag(
            f"{difficulty} allows at most {rubric.intended_path_max} "
            f"intended_path steps; design has {step_count}. Trim filler."
        )

    if rubric.needs_business_scenario:
        try:
            _require_business_scenario(challenge, parent_task)
        except ChallengeDesignValidationError as exc:
            _flag(str(exc))

    if rubric.needs_implementation_plan:
        plan = challenge.get("implementation_plan")
        if not isinstance(plan, Mapping) or not plan:
            _flag(f"{difficulty} requires a non-empty implementation_plan")

    # Buildability cap: count only explicitly declared build/deploy components.
    # Top-level keys such as runtime, framework, entrypoints, and flag_handling
    # are descriptive metadata and are not a valid proxy for implementation
    # complexity.
    plan = challenge.get("implementation_plan")
    components = plan.get("components") if isinstance(plan, Mapping) else None
    component_count = len(components) if isinstance(components, list) else 0
    if component_count > rubric.implementation_component_max:
        _flag(
            f"{difficulty} allows at most {rubric.implementation_component_max} "
            "explicit implementation_plan.components entries; design has "
            f"{component_count}. Split the design or upgrade the difficulty tier."
        )

    if rubric.needs_novelty:
        novelty = challenge.get("novelty")
        if not isinstance(novelty, str) or len(novelty.strip()) < MIN_NOVELTY_CHARS:
            _flag(
                f"expert difficulty requires a `novelty` field of at least "
                f"{MIN_NOVELTY_CHARS} characters describing the non-trivial trick"
            )

    if rubric.needs_unique_solution:
        unintended = challenge.get("unintended_solutions")
        valid = isinstance(unintended, list) and any(
            isinstance(item, str) and item.strip() for item in unintended
        )
        if not valid:
            _flag(
                f"{difficulty} requires a single intended solve path: list every "
                "considered alternate/unintended solution and how the design "
                "blocks it in a non-empty `unintended_solutions` array (easy may "
                "omit it and allow multiple paths)"
            )

    if rubric.min_asset_transitions > 0:
        reason = challenge.get("difficulty_reason")
        if (
            not isinstance(reason, str)
            or len(reason.strip()) < MIN_DIFFICULTY_REASON_CHARS
        ):
            _flag(
                f"{difficulty} requires a substantive `difficulty_reason` "
                "explaining why the declared chain matches the claimed tier"
            )

        closures = challenge.get("shortcut_closure")
        if not (
            isinstance(closures, list)
            and any(isinstance(item, str) and item.strip() for item in closures)
        ):
            _flag(
                f"{difficulty} requires non-empty `shortcut_closure` entries "
                "covering direct flag access, client-side gates, guessable "
                "tokens/URLs/IDs/seeds, public flag exposure, or similar "
                "collapse paths"
            )

        fingerprint = challenge.get("fingerprint")
        if not _valid_fingerprint(fingerprint):
            _flag(
                f"{difficulty} requires `fingerprint` with non-empty "
                "entrypoint_type, asset_flow_shape, flag_access_model, and "
                "scenario_type"
            )

        transitions = _count_asset_transitions(challenge)
        if transitions < rubric.min_asset_transitions:
            _flag(
                f"{difficulty} requires a required asset/capability chain with at "
                f"least {rubric.min_asset_transitions} effective transition(s); "
                f"`asset_flow` declares {transitions}. Each stage must produce a "
                "concrete asset/capability that the next stage requires (a stage "
                "needs both `produced_asset_or_capability` and "
                "`why_next_stage_requires_it`). easy may omit asset_flow."
            )

    if rubric.needs_actual_solution_type:
        from domain.design.shortcuts import is_forbidden_shortcut

        category = str(challenge.get("category") or "")
        declared = challenge.get("actual_solution_type")
        types = [s for s in declared if isinstance(s, str) and s.strip()] if (
            isinstance(declared, list)
        ) else []
        if not types:
            _flag(
                f"{difficulty} requires a non-empty `actual_solution_type` "
                "declaring how the challenge is really solved (so the nominal "
                "concept cannot be decorative). easy may omit it."
            )
        else:
            collapsed = [t for t in types if is_forbidden_shortcut(category, t)]
            if collapsed:
                _flag(
                    "declared actual_solution_type is itself a known collapse "
                    f"shortcut for {category}: {', '.join(collapsed)}. The real "
                    "solve must exercise the nominal technique, not a generic "
                    "shortcut."
                )

    if not violations:
        return

    if enforcement == _ENFORCEMENT_LENIENT:
        for message in violations:
            _LOGGER.warning(
                "design difficulty soft-passed for challenge %s (%s): %s "
                "(set DESIGN_DIFFICULTY_ENFORCEMENT=strict to reject)",
                challenge.get("id", "<unknown>"),
                difficulty,
                message,
            )
        return

    # Strict (default): surface the first violation so the operator sees
    # a single, actionable error rather than a wall of bullets.
    raise ChallengeDesignValidationError(violations[0])


# ---------- Counting helpers ----------


def _count_techniques(challenge: Mapping[str, Any]) -> int:
    """Count distinct non-mechanical sub-techniques in a design.

    Pure mechanical decode/unwrap labels are free alongside a real technique.
    If every declared technique is mechanical, the chain counts as one
    ``encoding`` technique.
    """
    distinct_non_mechanical: set[str] = set()
    saw_mechanical = False

    for label in _iter_declared_technique_labels(challenge):
        sub_technique = resolve_sub_technique({"label": label})
        if is_mechanical_transform(sub_technique):
            saw_mechanical = True
        else:
            distinct_non_mechanical.add(sub_technique)

    if distinct_non_mechanical:
        return len(distinct_non_mechanical)
    if saw_mechanical:
        return 1
    return 0


def _iter_declared_technique_labels(challenge: Mapping[str, Any]) -> Iterator[str]:
    raw_list = challenge.get("techniques")
    if isinstance(raw_list, list):
        for entry in raw_list:
            if isinstance(entry, str) and entry.strip():
                yield entry

    for field in ("primary_technique", "secondary_technique"):
        value = challenge.get(field)
        if isinstance(value, str) and value.strip():
            yield value


def _count_intended_path_steps(challenge: Mapping[str, Any]) -> int:
    raw = challenge.get("intended_path")
    if not isinstance(raw, list):
        return 0
    return sum(1 for entry in raw if isinstance(entry, str) and entry.strip())


def _count_asset_transitions(challenge: Mapping[str, Any]) -> int:
    """Count effective asset-flow transitions.

    A transition is effective only when a stage both produces a concrete
    asset/capability (``produced_asset_or_capability``) and states why the
    next stage requires it (``why_next_stage_requires_it``). Story-filler
    stages that produce nothing required do not count toward difficulty.
    """
    raw = challenge.get("asset_flow")
    if not isinstance(raw, list):
        return 0
    transitions = 0
    for stage in raw:
        if not isinstance(stage, Mapping):
            continue
        produced = stage.get("produced_asset_or_capability")
        why = stage.get("why_next_stage_requires_it")
        if (
            isinstance(produced, str)
            and _is_concrete_asset(produced)
            and isinstance(why, str)
            and _is_specific_dependency(why)
        ):
            transitions += 1
    return transitions


def _is_concrete_asset(value: str) -> bool:
    normalized = " ".join(value.strip().lower().split())
    if not normalized or normalized in _GENERIC_ASSET_WORDS:
        return False
    return len(normalized) >= 8 or len(normalized.split()) >= 2


def _is_specific_dependency(value: str) -> bool:
    normalized = " ".join(value.strip().lower().split())
    if not normalized or normalized in _GENERIC_DEPENDENCY_PHRASES:
        return False
    return len(normalized) >= 20 or len(normalized.split()) >= 4


def _valid_fingerprint(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    for field in (
        "entrypoint_type",
        "asset_flow_shape",
        "flag_access_model",
        "scenario_type",
    ):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            return False
    return True


def _require_business_scenario(
    challenge: Mapping[str, Any], parent_task: DesignTask
) -> None:
    prompt = challenge.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt.strip()) < MIN_BUSINESS_PROMPT_CHARS:
        raise ChallengeDesignValidationError(
            f"medium-and-up difficulty requires a player prompt of at least "
            f"{MIN_BUSINESS_PROMPT_CHARS} characters that conveys the business "
            "scenario"
        )
    if not isinstance(parent_task.scenario, str) or not parent_task.scenario.strip():
        raise ChallengeDesignValidationError(
            "medium-and-up difficulty requires a non-empty scenario on the "
            "parent design task"
        )
