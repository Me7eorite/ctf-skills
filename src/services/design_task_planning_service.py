"""Convert a researched generation request into design task rows.

Section 4 of the ``add-design-task-planning`` change. The service is
the only place that knows how to assemble candidate task rows from a
generation request + its completed research run + findings/sources.

Phase 2 of the design-skill rework (D5=b) added an *optional* Hermes
planner for hard and expert tasks: when injected via
``hermes_planner=``, the service calls it once per hard/expert task to
lock the technique chain and the business scenario seed before the full
design call runs. Easy and medium tasks stay fully deterministic. When
the planner is absent or returns ``None``, the difficulty-aware
templates below are the sole source of scenario/finding allocation.

The service deliberately does NOT render any design-output prompt text.
Prompt rendering happens at design-execution time.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa

from core.jsonio import read_json
from core.paths import ProjectPaths
from domain import design_tasks as dto
from domain import research as research_dto
from domain.design.technique_taxonomy import resolve_family, resolve_sub_technique
from domain.design_task_validators import DesignTaskValidationError
from domain.research_validators import (
    _quality_ratio,
    _quality_soft_pass_slack,
)
from persistence.models import design_tasks as design_model
from persistence.repositories import DesignTaskRepository, ResearchRepository
from persistence.session import SessionFactory, transaction
from services.design_planner_hermes import HermesPlannerService, PlannerEnrichment

_LOGGER = logging.getLogger(__name__)

# Reasonable default scoring per difficulty. Operators can override
# later via custom constraints on the request; this change does not
# expose any operator-facing knob for points.
DEFAULT_POINTS: Mapping[str, int] = {
    "easy": 100,
    "medium": 200,
    "hard": 300,
    "expert": 500,
}
DEFAULT_PORT_BASE = 9000
DEFAULT_COOLDOWN_WINDOW = 1

DIVERSITY_WARNING_FAMILY_QUOTA = "family_quota_exceeded"
DIVERSITY_WARNING_SUBTECHNIQUE_DUPLICATE = "subtechnique_duplicate"
DIVERSITY_WARNING_FAMILY_OTHER = "family_other"


class DesignTaskPlanningService:
    """Generate design tasks for researched generation requests."""

    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        *,
        hermes_planner: HermesPlannerService | None = None,
    ) -> None:
        # Same shape as ResearchJobService: the service owns one short
        # transaction per public method and never holds a long-lived
        # session.
        self.session_factory = session_factory
        self.hermes_planner = hermes_planner

    def generate_for_request(self, request_id: UUID) -> list[dto.DesignTask]:
        """Replace draft/archived tasks with a freshly planned set.

        Raises :class:`DesignTaskValidationError` if the parent request
        does not exist, has not been researched, or has any task whose
        status is past ``draft``/``archived``.
        """
        with transaction(factory=self.session_factory) as session:
            research_repo = ResearchRepository(session)
            design_repo = DesignTaskRepository(session)

            request = research_repo.get_generation_request(request_id)
            if request is None:
                raise DesignTaskValidationError(
                    f"generation_request {request_id} does not exist"
                )
            # 锁住父行：当请求当前没有任何 design_tasks 时，repository
            # 的 SELECT ... FOR UPDATE 锁不到任何行，两个并发 generate
            # 会都通过校验后才在 INSERT 阶段撞到唯一约束。父行锁让这种
            # 场景串行化为干净的 409，而非 5xx 完整性错误。
            research_repo.lock_generation_request(request_id)

            latest = research_repo.get_latest_run_for_request(request_id)
            if latest is None or latest.status != "completed":
                raise DesignTaskValidationError("latest_run_not_completed")

            findings = research_repo.list_findings(latest.id)
            sources = research_repo.list_sources(latest.id)
            # Same gate as research_validators.apply_research_quality_gate
            # so RESEARCH_QUALITY_RATIO / RESEARCH_QUALITY_SOFT_PASS_BELOW_BY
            # honor both call sites. Without this, a research run could
            # soft-pass at completion time but then be rejected by the
            # planner with a different error message.
            needed = max(1, math.ceil(request.target_count * _quality_ratio()))
            soft_floor = max(1, needed - _quality_soft_pass_slack())
            if len(findings) < soft_floor:
                raise DesignTaskValidationError("insufficient_findings")
            if not findings or not sources:
                raise DesignTaskValidationError(
                    "completed research run has no sources or findings; "
                    "cannot generate design tasks"
                )

            candidates = _plan_candidates(
                request, latest, findings, hermes_planner=self.hermes_planner
            )
            # 跨表校验：design.md §Task Generation Flow 第 5 步要求
            # “每个 task 必须引用至少一个来自当前 research_run 的 finding”。
            # 这一规则需要 SELECT，所以放在 service 层（validators 模块
            # 注明它只做无 SELECT 的形状校验）。
            validate_finding_provenance(
                candidates,
                allowed_finding_ids={f.id for f in findings},
                research_run_id=latest.id,
            )
            return design_repo.replace_draft_or_archived_tasks(
                generation_request_id=request.id,
                research_run_id=latest.id,
                parent_category=request.category,
                target_count=request.target_count,
                difficulty_distribution=request.difficulty_distribution,
                candidates=candidates,
            )

    def approve_plan(self, request_id: UUID) -> list[dto.DesignTask]:
        """Stamp all current draft tasks as reviewed under the parent request lock."""
        with transaction(factory=self.session_factory) as session:
            research_repo = ResearchRepository(session)
            request = research_repo.get_generation_request(request_id)
            if request is None:
                raise DesignTaskValidationError(f"generation_request {request_id} does not exist")
            research_repo.lock_generation_request(request_id)
            rows = _locked_task_rows(session, request_id)
            if not rows:
                raise DesignTaskValidationError("no design tasks to approve")
            for row in rows:
                if row.status != "draft":
                    raise DesignTaskValidationError("plan approval requires all tasks to be draft")
            now = _utcnow()
            for row in rows:
                row.plan_reviewed_at = now
                row.updated_at = now
            session.flush()
            return [_row_to_dto(row) for row in rows]

    def regenerate_plan(self, request_id: UUID) -> list[dto.DesignTask]:
        """Regenerate the whole draft plan and clear review markers on new rows."""
        return self.generate_for_request(request_id)

    def regenerate_task(self, request_id: UUID, task_no: int) -> dict[str, Any]:
        """Regenerate one draft task slot, or return a typed no-op outcome."""
        if task_no <= 0:
            raise DesignTaskValidationError("task_no must be positive")
        with transaction(factory=self.session_factory) as session:
            research_repo = ResearchRepository(session)
            request = research_repo.get_generation_request(request_id)
            if request is None:
                raise DesignTaskValidationError(f"generation_request {request_id} does not exist")
            research_repo.lock_generation_request(request_id)
            rows = _locked_task_rows(session, request_id)
            if not rows:
                raise DesignTaskValidationError("no design tasks to regenerate")
            if any(row.status not in {"draft", "archived"} for row in rows):
                raise DesignTaskValidationError("cannot regenerate after queue release")
            current = next((row for row in rows if row.task_no == task_no), None)
            if current is None:
                raise DesignTaskValidationError(f"task_no {task_no} does not exist")

            latest = research_repo.get_latest_completed_run_for_request(request_id)
            if latest is None:
                raise DesignTaskValidationError("latest_run_not_completed")
            findings = research_repo.list_findings(latest.id)
            if not findings:
                raise DesignTaskValidationError("insufficient_findings")

            profile = _diversity_profile(request.category, request.target_count, findings)
            sibling_flags = [
                row.diversity_flags or {}
                for row in rows
                if row.task_no != task_no and row.status in {"draft", "archived"}
            ]
            sibling_subtechniques = {
                str(flags.get("sub_technique"))
                for flags in sibling_flags
                if flags.get("sub_technique")
            }
            sibling_family_counts: Counter[str] = Counter(
                str(flags.get("family"))
                for flags in sibling_flags
                if flags.get("family")
            )
            current_flags = current.diversity_flags or {}
            current_pair = (
                str(
                    current_flags.get("family")
                    or resolve_family({"label": current.primary_technique}, category=request.category)
                ),
                str(
                    current_flags.get("sub_technique")
                    or resolve_sub_technique({"label": current.primary_technique})
                ),
            )

            metadata = [
                (
                    idx,
                    resolve_family(finding, category=request.category),
                    resolve_sub_technique(finding),
                )
                for idx, finding in enumerate(findings)
            ]
            distinct_other_than_current = [
                item for item in metadata if (item[1], item[2]) != current_pair
            ]
            if not distinct_other_than_current:
                return {
                    "outcome": "no_alternative",
                    "reason": "research_diversity_insufficient",
                    "task": _row_to_dto(current),
                }
            sibling_avoiding = [
                item for item in distinct_other_than_current if item[2] not in sibling_subtechniques
            ]
            if not sibling_avoiding:
                return {
                    "outcome": "no_alternative",
                    "reason": "subtechnique_exhausted",
                    "task": _row_to_dto(current),
                }

            within_family = [
                item for item in sibling_avoiding if sibling_family_counts[item[1]] < profile.technique_quota
            ]
            chosen_idx, family, sub_technique = (within_family or sibling_avoiding)[0]
            warnings: list[str] = []
            outcome: Literal["regenerated", "regenerated_with_warning"] = "regenerated"
            if not within_family:
                warnings.append(DIVERSITY_WARNING_FAMILY_QUOTA)
                outcome = "regenerated_with_warning"
            if family == "other":
                warnings.append(DIVERSITY_WARNING_FAMILY_OTHER)
            candidate = _candidate_for_slot(
                request=request,
                run=latest,
                task_no=current.task_no,
                difficulty=current.difficulty,
                primary_index=chosen_idx,
                findings=findings,
                diversity_flags={
                    "family": family,
                    "sub_technique": sub_technique,
                    "warnings": warnings,
                },
                avoid_techniques=sibling_subtechniques,
                hermes_planner=self.hermes_planner,
            )
            _apply_candidate_to_row(current, candidate)
            current.plan_reviewed_at = None
            current.updated_at = _utcnow()
            session.flush()
            session.refresh(current)
            return {"outcome": outcome, "task": _row_to_dto(current)}


def validate_finding_provenance(
    candidates: Iterable[Mapping[str, Any]],
    *,
    allowed_finding_ids: set[UUID],
    research_run_id: UUID,
) -> None:
    """Reject candidates whose ``finding_ids`` are missing or off-run.

    Enforces the design contract that every generated design task cites
    at least one finding belonging to the same completed research run.
    The check is in the service (not :mod:`domain.design_task_validators`)
    because it requires the SELECT result of ``list_findings(run_id)``.
    """
    for candidate in candidates:
        raw = candidate.get("finding_ids") or ()
        if not raw:
            raise DesignTaskValidationError(
                f"task_no {candidate.get('task_no')!r} cites no finding from "
                f"research run {research_run_id}"
            )
        try:
            cited = {UUID(str(fid)) for fid in raw}
        except (TypeError, ValueError) as exc:
            raise DesignTaskValidationError(
                f"task_no {candidate.get('task_no')!r} has malformed "
                f"finding_ids {list(raw)!r}: {exc}"
            ) from exc
        foreign = sorted(str(fid) for fid in cited - allowed_finding_ids)
        if foreign:
            raise DesignTaskValidationError(
                f"task_no {candidate.get('task_no')!r} cites finding(s) "
                f"{foreign} not from research run {research_run_id}"
            )


# Phase 2 planner: scenario templates per difficulty tier. Medium and
# above MUST set a believable business scenario (validator requirement).
# Easy stays a "toy service" intentionally.
_SCENARIO_TEMPLATES: Mapping[str, str] = {
    "easy": (
        "Standalone {category} target demonstrating {technique}. "
        "Single-step solve."
    ),
    "medium": (
        "Internal business app (notes, tickets, reports, or admin review) "
        "in which {technique} is reachable through the normal user flow. "
        "{secondary_line}"
    ),
    "hard": (
        "Multi-stage {category} target. Players must chain {technique} with "
        "{secondary_technique} to reach the flag. {tertiary_line}"
    ),
    "expert": (
        "Multi-stage {category} chain with a non-trivial mechanic. "
        "{technique} exposes {secondary_technique}, which constrains "
        "{tertiary_technique}. Author MUST populate `novelty` describing the "
        "0day-style trick or unusual constraint."
    ),
}

# How many findings to draw for one task at each difficulty.
_FINDINGS_PER_DIFFICULTY: Mapping[str, int] = {
    "easy": 1,
    "medium": 1,
    "hard": 2,
    "expert": 3,
}


def _plan_candidates(
    request: research_dto.GenerationRequest,
    run: research_dto.ResearchRun,
    findings: Sequence[research_dto.ResearchFinding],
    *,
    hermes_planner: HermesPlannerService | None = None,
) -> list[dict[str, Any]]:
    """Deterministic planner: 1 candidate per target slot.

    Phase 2 changed from a simple round-robin to a difficulty-aware
    allocation. easy/medium tasks get one finding; hard tasks pull two;
    expert tasks pull three. The extra findings are folded into
    ``scenario``, ``evidence_summary``, and ``finding_ids`` so the
    downstream Hermes design call has enough material to satisfy the
    difficulty rubric (≥ N techniques, business scenario, etc.).

    A future Hermes-backed planner replacing this function MUST keep the
    same output shape; the repository re-validates so silent breakage is
    not possible.
    """
    difficulty_slots: list[str] = []
    for difficulty, count in sorted(request.difficulty_distribution.items()):
        difficulty_slots.extend([difficulty] * int(count))
    if len(difficulty_slots) != request.target_count:
        raise DesignTaskValidationError(
            "difficulty_distribution sums to "
            f"{len(difficulty_slots)} but target_count is "
            f"{request.target_count}"
        )

    candidates: list[dict[str, Any]] = []
    category = request.category
    runtime_constraints = dict(request.runtime_constraints or {})
    profile = _diversity_profile(category, request.target_count, findings)
    allocations = _allocate_primary_findings(
        findings,
        target_count=request.target_count,
        category=category,
        technique_quota=profile.technique_quota,
        cooldown_window=profile.cooldown_window,
    )
    mechanisms = _allocate_core_mechanisms(category, len(difficulty_slots))
    for index, difficulty in enumerate(difficulty_slots):
        task_no = index + 1
        allocation = allocations[index]
        task_findings = _findings_for_task(difficulty, findings, allocation.index)
        primary = task_findings[0]
        secondaries = task_findings[1:]
        candidate: dict[str, Any] = {
            "task_no": task_no,
            "challenge_id": _challenge_id(category, request.id, task_no),
            "title": _title(request.topic, primary, task_no),
            "category": category,
            "difficulty": difficulty,
            "primary_technique": primary.label,
            "learning_objective": (
                f"Reproduce {primary.label} on a {category} target "
                f"derived from research run {run.attempt}."
            ),
            "points": DEFAULT_POINTS.get(difficulty, 100),
            "port": _port_for(category, task_no),
            "scenario": _scenario_for(category, difficulty, task_findings),
            "constraints": dict(runtime_constraints),
            "evidence_summary": _evidence_summary(run, request.topic, task_findings),
            "finding_ids": [f.id for f in task_findings],
            "diversity_flags": {
                **dict(allocation.diversity_flags),
                "core_mechanism": mechanisms[index],
            },
        }
        # Phase 2 (D5=b): for hard/expert tasks the optional Hermes
        # planner can replace the template scenario with a Hermes-locked
        # technique chain. Failure is non-fatal — we keep the template
        # values and tag the candidate so the operator can see why.
        if hermes_planner is not None and difficulty in {"hard", "expert"}:
            enrichment = hermes_planner.plan(
                category=category,
                difficulty=difficulty,
                topic=request.topic,
                primary=primary,
                secondaries=secondaries,
                avoid_techniques=sorted(allocation.avoid_techniques),
            )
            if enrichment is not None:
                _apply_planner_enrichment(candidate, enrichment)
            else:
                candidate["constraints"]["_planner_source"] = "template_fallback"
        candidates.append(candidate)
    return candidates


def _apply_planner_enrichment(
    candidate: dict[str, Any], enrichment: PlannerEnrichment
) -> None:
    """Merge Hermes planner output into a candidate task row."""
    candidate["scenario"] = enrichment.scenario_seed
    candidate["evidence_summary"] = (
        candidate["evidence_summary"]
        + " Planner chain: "
        + enrichment.chain_outline
    )
    candidate["constraints"]["_planner_source"] = "hermes"
    candidate["constraints"]["_planner_techniques"] = list(
        enrichment.considered_techniques
    )
    if enrichment.novelty_seed:
        candidate["constraints"]["_novelty_seed"] = enrichment.novelty_seed


def _candidate_for_slot(
    *,
    request: research_dto.GenerationRequest,
    run: research_dto.ResearchRun,
    task_no: int,
    difficulty: str,
    primary_index: int,
    findings: Sequence[research_dto.ResearchFinding],
    diversity_flags: Mapping[str, Any],
    avoid_techniques: Iterable[str] = (),
    hermes_planner: HermesPlannerService | None = None,
) -> dict[str, Any]:
    category = request.category
    task_findings = _findings_for_task(difficulty, findings, primary_index)
    primary = task_findings[0]
    secondaries = task_findings[1:]
    candidate: dict[str, Any] = {
        "task_no": task_no,
        "challenge_id": _challenge_id(category, request.id, task_no),
        "title": _title(request.topic, primary, task_no),
        "category": category,
        "difficulty": difficulty,
        "primary_technique": primary.label,
        "learning_objective": (
            f"Reproduce {primary.label} on a {category} target "
            f"derived from research run {run.attempt}."
        ),
        "points": DEFAULT_POINTS.get(difficulty, 100),
        "port": _port_for(category, task_no),
        "scenario": _scenario_for(category, difficulty, task_findings),
        "constraints": dict(request.runtime_constraints or {}),
        "evidence_summary": _evidence_summary(run, request.topic, task_findings),
        "finding_ids": [f.id for f in task_findings],
        "diversity_flags": dict(diversity_flags),
    }
    if hermes_planner is not None and difficulty in {"hard", "expert"}:
        enrichment = hermes_planner.plan(
            category=category,
            difficulty=difficulty,
            topic=request.topic,
            primary=primary,
            secondaries=secondaries,
            avoid_techniques=sorted(str(item) for item in avoid_techniques),
        )
        if enrichment is not None:
            _apply_planner_enrichment(candidate, enrichment)
        else:
            candidate["constraints"]["_planner_source"] = "template_fallback"
    return candidate


def _apply_candidate_to_row(row: design_model.DesignTask, candidate: Mapping[str, Any]) -> None:
    row.challenge_id = str(candidate["challenge_id"])
    row.title = str(candidate["title"])
    row.category = str(candidate["category"])
    row.difficulty = str(candidate["difficulty"])
    row.primary_technique = str(candidate["primary_technique"])
    row.learning_objective = str(candidate["learning_objective"])
    row.points = int(candidate["points"])
    row.port = candidate.get("port")
    row.scenario = str(candidate.get("scenario", ""))
    row.constraints = dict(candidate.get("constraints") or {})
    row.evidence_summary = str(candidate.get("evidence_summary", ""))
    row.finding_ids = [str(fid) for fid in candidate.get("finding_ids") or ()]
    row.diversity_flags = (
        dict(candidate["diversity_flags"])
        if candidate.get("diversity_flags") is not None
        else None
    )


def _locked_task_rows(session, request_id: UUID) -> list[design_model.DesignTask]:
    return list(
        session.scalars(
            sa.select(design_model.DesignTask)
            .where(design_model.DesignTask.generation_request_id == request_id)
            .order_by(design_model.DesignTask.task_no)
            .with_for_update()
        ).all()
    )


def _row_to_dto(row: design_model.DesignTask) -> dto.DesignTask:
    return dto.DesignTask(
        id=row.id,
        generation_request_id=row.generation_request_id,
        research_run_id=row.research_run_id,
        task_no=row.task_no,
        challenge_id=row.challenge_id,
        title=row.title,
        category=row.category,
        difficulty=row.difficulty,
        primary_technique=row.primary_technique,
        learning_objective=row.learning_objective,
        points=row.points,
        port=row.port,
        scenario=row.scenario,
        constraints=dict(row.constraints),
        evidence_summary=row.evidence_summary,
        finding_ids=tuple(UUID(str(fid)) for fid in row.finding_ids),
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        diversity_flags=(dict(row.diversity_flags) if row.diversity_flags is not None else None),
        plan_reviewed_at=row.plan_reviewed_at,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _findings_for_task(
    difficulty: str,
    findings: Sequence[research_dto.ResearchFinding],
    primary_index: int,
) -> list[research_dto.ResearchFinding]:
    """Pick ``n`` findings for this task in stable, deterministic order.

    When the research run produced fewer than ``n`` findings the planner
    reuses earlier entries rather than failing — the downstream Hermes
    design call can still rebrand the secondary technique as long as the
    primary finding remains distinct.
    """
    need = _FINDINGS_PER_DIFFICULTY.get(difficulty, 1)
    pool_size = len(findings)
    if pool_size == 0:
        raise DesignTaskValidationError(
            "_plan_candidates called with no findings"
        )
    return [findings[(primary_index + offset) % pool_size] for offset in range(need)]


@dataclass(frozen=True)
class _DiversityProfile:
    technique_quota: int
    cooldown_window: int


@dataclass(frozen=True)
class _FindingAllocation:
    index: int
    diversity_flags: dict[str, Any]
    avoid_techniques: frozenset[str]


def _allocate_primary_findings(
    findings: Sequence[research_dto.ResearchFinding],
    *,
    target_count: int,
    category: str,
    technique_quota: int,
    cooldown_window: int,
) -> list[_FindingAllocation]:
    pool_size = len(findings)
    if pool_size == 0:
        raise DesignTaskValidationError("_plan_candidates called with no findings")

    metadata = [
        {
            "family": resolve_family(finding, category=category),
            "sub_technique": resolve_sub_technique(finding),
        }
        for finding in findings
    ]
    distinct_subtechniques = {item["sub_technique"] for item in metadata}
    duplicate_unavoidable = len(distinct_subtechniques) < target_count

    family_counts: Counter[str] = Counter()
    used_subtechniques: set[str] = set()
    recent_families: list[str] = []
    allocations: list[_FindingAllocation] = []

    for task_index in range(target_count):
        ordered_indices = [(task_index + offset) % pool_size for offset in range(pool_size)]
        quota_candidates = [
            idx
            for idx in ordered_indices
            if family_counts[metadata[idx]["family"]] < technique_quota
        ]
        family_quota_exceeded = False
        if not quota_candidates:
            quota_candidates = ordered_indices
            family_quota_exceeded = True

        cooldown_candidates = [
            idx
            for idx in quota_candidates
            if metadata[idx]["family"] not in recent_families[-cooldown_window:]
        ] if cooldown_window > 0 else quota_candidates
        family_candidates = cooldown_candidates or quota_candidates

        unused_sub_candidates = [
            idx
            for idx in family_candidates
            if metadata[idx]["sub_technique"] not in used_subtechniques
        ]
        if unused_sub_candidates:
            chosen = unused_sub_candidates[0]
            subtechnique_duplicate = duplicate_unavoidable
        else:
            chosen = family_candidates[0]
            subtechnique_duplicate = True

        family = metadata[chosen]["family"]
        sub_technique = metadata[chosen]["sub_technique"]
        warnings: list[str] = []
        if family_quota_exceeded:
            warnings.append(DIVERSITY_WARNING_FAMILY_QUOTA)
        if subtechnique_duplicate:
            warnings.append(DIVERSITY_WARNING_SUBTECHNIQUE_DUPLICATE)
        if family == "other":
            warnings.append(DIVERSITY_WARNING_FAMILY_OTHER)

        allocations.append(
            _FindingAllocation(
                index=chosen,
                diversity_flags={
                    "family": family,
                    "sub_technique": sub_technique,
                    "warnings": warnings,
                },
                avoid_techniques=frozenset(used_subtechniques),
            )
        )
        family_counts[family] += 1
        used_subtechniques.add(sub_technique)
        recent_families.append(family)
    return allocations


# Per-category implementation/flag-protection mechanism catalogs. This is the
# "core mechanism" axis that prevents implementation collapse (e.g. every RE
# challenge defaulting to XOR). Operators can override via
# generation-profiles.json -> categories.<cat>.mechanisms.
_DEFAULT_MECHANISMS: dict[str, tuple[str, ...]] = {
    "re": (
        "xor_keystream",
        "arithmetic_chain",
        "sbox_substitution",
        "tea_xtea",
        "rc4",
        "aes",
        "hash_compare",
        "per_char_permutation",
        "lcg_prng_keystream",
        "fsm_or_vm_check",
    ),
    "pwn": (
        "stack_ret2win_disabled",
        "ret2libc_leak",
        "format_string_got",
        "heap_uaf_tcache",
        "heap_overlap",
        "integer_oob",
        "srop",
        "seccomp_orw",
    ),
    "web": (
        "ssti_to_secret",
        "ssrf_internal_service",
        "deserialization_gadget",
        "jwt_algorithm_confusion",
        "upload_server_side_parse",
        "blind_sqli_exfil",
        "idor_cross_user",
        "business_logic_state",
    ),
}


def _mechanisms_for_category(category: str) -> tuple[str, ...]:
    configured = _generation_profile_category(category).get("mechanisms")
    if isinstance(configured, list):
        cleaned = tuple(m for m in configured if isinstance(m, str) and m.strip())
        if cleaned:
            return cleaned
    return _DEFAULT_MECHANISMS.get(category, ("generic_gate",))


def _allocate_core_mechanisms(category: str, count: int) -> list[str]:
    """Deterministically rotate a flag-protection / core mechanism per task.

    Round-robin over the category catalog so a batch spreads evenly across
    mechanisms instead of every task collapsing to the same default (XOR for
    RE, win-function for PWN, weak-creds for Web). Deterministic -> testable and
    reproducible; the assigned mechanism is later injected as a binding design
    constraint.
    """
    catalog = _mechanisms_for_category(category)
    return [catalog[i % len(catalog)] for i in range(max(0, count))]


def _diversity_profile(
    category: str,
    target_count: int,
    findings: Sequence[research_dto.ResearchFinding],
) -> _DiversityProfile:
    configured = _generation_profile_category(category)
    distinct_families = {
        resolve_family(finding, category=category)
        for finding in findings
    }
    default_quota = max(1, math.ceil(target_count / max(1, len(distinct_families))))
    technique_quota = _positive_int(configured.get("technique_quota"), default_quota)
    cooldown_window = _nonnegative_int(
        configured.get("cooldown_window"),
        DEFAULT_COOLDOWN_WINDOW,
    )
    return _DiversityProfile(
        technique_quota=technique_quota,
        cooldown_window=cooldown_window,
    )


def _generation_profile_category(category: str) -> Mapping[str, Any]:
    try:
        path = ProjectPaths.discover().generation_profile
        payload = read_json(path, {})
    except Exception as exc:  # noqa: BLE001 - missing/malformed profile should not block planning
        _LOGGER.warning("could not read generation profile: %s", exc)
        return {}
    if not isinstance(payload, Mapping):
        return {}
    categories = payload.get("categories")
    if not isinstance(categories, Mapping):
        return {}
    row = categories.get(category)
    return row if isinstance(row, Mapping) else {}


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


def _scenario_for(
    category: str,
    difficulty: str,
    task_findings: Sequence[research_dto.ResearchFinding],
) -> str:
    template = _SCENARIO_TEMPLATES.get(difficulty, _SCENARIO_TEMPLATES["easy"])
    primary = task_findings[0]
    secondary = task_findings[1] if len(task_findings) > 1 else None
    tertiary = task_findings[2] if len(task_findings) > 2 else None
    return template.format(
        category=category,
        technique=primary.label,
        secondary_technique=(secondary.label if secondary else primary.label),
        tertiary_technique=(tertiary.label if tertiary else primary.label),
        secondary_line=(secondary.summary if secondary else ""),
        tertiary_line=(tertiary.summary if tertiary else ""),
    ).strip()


def _evidence_summary(
    run: research_dto.ResearchRun,
    topic: str,
    task_findings: Sequence[research_dto.ResearchFinding],
) -> str:
    labels = ", ".join(sorted({f.label for f in task_findings}))
    return (
        f"{len(task_findings)} finding(s) cited from research run {run.id} "
        f"on topic {topic!r}: {labels}."
    )


def _title(topic: str, finding: research_dto.ResearchFinding, task_no: int) -> str:
    base = topic.strip() or finding.label
    return f"{base} — task {task_no}"


def _challenge_id(category: str, request_id: UUID, task_no: int) -> str:
    return f"{category}-{request_id.hex[:8]}-{task_no:04d}"


def _port_for(category: str, task_no: int) -> int | None:
    if category in {"web", "pwn"}:
        return DEFAULT_PORT_BASE + task_no
    return None
