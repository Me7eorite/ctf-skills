"""Difficulty-aware content alignment for validated design payloads.

Phase 2 of the design-skill rework: the structural validator (see
:mod:`domain.design.validator`) only enforces field shapes. Difficulty
alignment is checked here so the rules stay readable and so the per-tier
table is the single source of truth.

The tier table mirrors ``skills/design-challenges/references/difficulty-rubric.md``
verbatim; change one and update the other.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from domain.design.schema import ChallengeDesignValidationError
from domain.design_tasks import DesignTask

# Minimum length for the player prompt at medium and above. A one-liner
# is too thin to carry business context.
MIN_BUSINESS_PROMPT_CHARS = 60

# Expert ``novelty`` must be substantive enough to identify the trick.
MIN_NOVELTY_CHARS = 40


@dataclass(frozen=True)
class DifficultyRubric:
    """Machine-checked thresholds for one difficulty tier."""

    techniques_min: int
    techniques_max: int  # inclusive; use a large sentinel for "no upper bound"
    intended_path_min: int
    intended_path_max: int
    needs_business_scenario: bool
    needs_implementation_plan: bool
    needs_novelty: bool


RUBRIC: dict[str, DifficultyRubric] = {
    "easy": DifficultyRubric(
        techniques_min=1,
        techniques_max=1,
        intended_path_min=1,
        intended_path_max=3,
        needs_business_scenario=False,
        needs_implementation_plan=False,
        needs_novelty=False,
    ),
    "medium": DifficultyRubric(
        techniques_min=2,
        techniques_max=3,
        intended_path_min=2,
        intended_path_max=5,
        needs_business_scenario=True,
        needs_implementation_plan=False,
        needs_novelty=False,
    ),
    "hard": DifficultyRubric(
        techniques_min=3,
        techniques_max=4,
        intended_path_min=3,
        intended_path_max=7,
        needs_business_scenario=True,
        needs_implementation_plan=True,
        needs_novelty=False,
    ),
    "expert": DifficultyRubric(
        techniques_min=2,
        techniques_max=99,
        intended_path_min=4,
        intended_path_max=10,
        needs_business_scenario=True,
        needs_implementation_plan=True,
        needs_novelty=True,
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

    technique_count = _count_techniques(challenge)
    if technique_count < rubric.techniques_min:
        raise ChallengeDesignValidationError(
            f"{difficulty} requires at least {rubric.techniques_min} distinct "
            f"techniques; design only declares {technique_count}"
        )
    if technique_count > rubric.techniques_max:
        raise ChallengeDesignValidationError(
            f"{difficulty} allows at most {rubric.techniques_max} distinct "
            f"techniques; design declares {technique_count}. Split or downgrade."
        )

    step_count = _count_intended_path_steps(challenge)
    if step_count < rubric.intended_path_min:
        raise ChallengeDesignValidationError(
            f"{difficulty} requires at least {rubric.intended_path_min} "
            f"intended_path steps; design has {step_count}"
        )
    if step_count > rubric.intended_path_max:
        raise ChallengeDesignValidationError(
            f"{difficulty} allows at most {rubric.intended_path_max} "
            f"intended_path steps; design has {step_count}. Trim filler."
        )

    if rubric.needs_business_scenario:
        _require_business_scenario(challenge, parent_task)

    if rubric.needs_implementation_plan:
        plan = challenge.get("implementation_plan")
        if not isinstance(plan, Mapping) or not plan:
            raise ChallengeDesignValidationError(
                f"{difficulty} requires a non-empty implementation_plan"
            )

    if rubric.needs_novelty:
        novelty = challenge.get("novelty")
        if not isinstance(novelty, str) or len(novelty.strip()) < MIN_NOVELTY_CHARS:
            raise ChallengeDesignValidationError(
                f"expert difficulty requires a `novelty` field of at least "
                f"{MIN_NOVELTY_CHARS} characters describing the non-trivial trick"
            )


# ---------- Counting helpers ----------


def _count_techniques(challenge: Mapping[str, Any]) -> int:
    """Union ``techniques``, ``primary_technique``, ``secondary_technique``.

    Comparison is case-insensitive and whitespace-trimmed so that
    ``["SQLi", "sqli"]`` does not pad the count.
    """
    distinct: set[str] = set()

    raw_list = challenge.get("techniques")
    if isinstance(raw_list, list):
        for entry in raw_list:
            if isinstance(entry, str) and entry.strip():
                distinct.add(entry.strip().lower())

    for field in ("primary_technique", "secondary_technique"):
        value = challenge.get(field)
        if isinstance(value, str) and value.strip():
            distinct.add(value.strip().lower())

    return len(distinct)


def _count_intended_path_steps(challenge: Mapping[str, Any]) -> int:
    raw = challenge.get("intended_path")
    if not isinstance(raw, list):
        return 0
    return sum(1 for entry in raw if isinstance(entry, str) and entry.strip())


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
