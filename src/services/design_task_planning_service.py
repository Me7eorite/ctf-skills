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
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from domain import design_tasks as dto
from domain import research as research_dto
from domain.design.profile_taxonomy import (
    canonical_profile_signatures,
    canonicalize_pwn_semantic_assignment,
    load_profile_policy,
    normalize_semantic_assignment,
    profile_capacity_check,
    taxonomy_for_category,
)
from domain.design.technique_taxonomy import resolve_family, resolve_sub_technique
from domain.design_task_validators import DesignTaskValidationError
from domain.generation_profile import category_profile_config
from domain.research_validators import (
    _quality_ratio,
    _quality_soft_pass_slack,
)
from persistence.models import challenge_designs as challenge_model
from persistence.models import design_tasks as design_model
from persistence.repositories import (
    BuildAttemptsRepository,
    ChallengeDesignRepository,
    DesignEvidenceRepository,
    DesignProfileReservationRepository,
    DesignTaskRepository,
    ResearchRepository,
)
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
LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"
GENERATION_REQUEST_BUSY_CODE = "generation_request_busy"
DESIGN_TASK_PERSISTENCE_FAILED_CODE = "design_task_persistence_failed"


@dataclass
class DesignTaskGenerationPersistenceError(RuntimeError):
    """Typed wrapper for persistence failures during design-task generation."""

    request_id: UUID
    stage: str
    message: str
    retryable: bool = True
    code: str = field(default=DESIGN_TASK_PERSISTENCE_FAILED_CODE, init=False)

    def __str__(self) -> str:
        return self.message


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
        active_request_id = request_id
        active_research_run_id: UUID | None = None

        def set_active_research_run_id(run_id: UUID) -> None:
            nonlocal active_research_run_id
            active_research_run_id = run_id

        try:
            return self._generate_for_request_in_transaction(
                request_id,
                set_active_research_run_id=set_active_research_run_id,
            )
        except DBAPIError as exc:
            _raise_persistence_failed(
                exc,
                request_id=active_request_id,
                research_run_id=active_research_run_id,
                stage="design_task_commit",
            )

    def _generate_for_request_in_transaction(
        self,
        request_id: UUID,
        *,
        set_active_research_run_id,
    ) -> list[dto.DesignTask]:
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
            _lock_generation_request_or_busy(research_repo, request_id)

            latest = research_repo.get_latest_completed_run_for_request(request_id)
            if latest is None:
                raise DesignTaskValidationError("latest_run_not_completed")
            set_active_research_run_id(latest.id)

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
            _LOGGER.info(
                "generate_design_tasks planned_candidate_count=%s request_id=%s research_run_id=%s stage=planning",
                len(candidates),
                request.id,
                latest.id,
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
            reservation_repo = DesignProfileReservationRepository(session)
            try:
                reservation_repo.release_active_for_request(request.id)
            except DBAPIError as exc:
                _raise_persistence_failed(
                    exc,
                    request_id=request.id,
                    research_run_id=latest.id,
                    stage="reservation_release",
                )
            try:
                created_tasks = design_repo.replace_draft_or_archived_tasks(
                    generation_request_id=request.id,
                    research_run_id=latest.id,
                    parent_category=request.category,
                    target_count=request.target_count,
                    difficulty_distribution=request.difficulty_distribution,
                    candidates=candidates,
                )
            except DBAPIError as exc:
                _raise_persistence_failed(
                    exc,
                    request_id=request.id,
                    research_run_id=latest.id,
                    stage="design_task_insert",
                )
            _LOGGER.info(
                "generate_design_tasks inserted_task_count=%s request_id=%s "
                "research_run_id=%s stage=design_task_insert",
                len(created_tasks),
                request.id,
                latest.id,
            )
            try:
                allocated_count = self._allocate_reservations(
                    session=session,
                    generation_request=request,
                    tasks=created_tasks,
                    candidates=candidates,
                )
            except DBAPIError as exc:
                _raise_persistence_failed(
                    exc,
                    request_id=request.id,
                    research_run_id=latest.id,
                    stage="reservation_allocation",
                )
            _LOGGER.info(
                "generate_design_tasks reservation_allocated_count=%s "
                "request_id=%s research_run_id=%s stage=reservation_allocation",
                allocated_count,
                request.id,
                latest.id,
            )
            return design_repo.list_design_tasks(request.id)

    def approve_plan(self, request_id: UUID) -> list[dto.DesignTask]:
        """Stamp all current draft tasks as reviewed under the parent request lock."""
        with transaction(factory=self.session_factory) as session:
            research_repo = ResearchRepository(session)
            request = research_repo.get_generation_request(request_id)
            if request is None:
                raise DesignTaskValidationError(f"generation_request {request_id} does not exist")
            _lock_generation_request_or_busy(research_repo, request_id)
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
            _lock_generation_request_or_busy(research_repo, request_id)
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
            reservation_repo = DesignProfileReservationRepository(session)
            if current.current_reservation_id is not None:
                reservation_repo.release_reservation(current.current_reservation_id)
            _apply_candidate_to_row(current, candidate)
            current.plan_reviewed_at = None
            current.updated_at = _utcnow()
            session.flush()
            session.refresh(current)
            self._allocate_reservations(
                session=session,
                generation_request=request,
                tasks=[current],
                candidates=[candidate],
            )
            return {"outcome": outcome, "task": _row_to_dto(current)}

    def request_design_revision(
        self,
        design_task_id: UUID,
        *,
        reason: str,
    ) -> dto.DesignTask:
        """Supersede the current governed design and return the task to draft."""
        if not reason.strip():
            raise DesignTaskValidationError("revision reason is required")
        with transaction(factory=self.session_factory) as session:
            task_request_id = session.scalar(
                sa.select(design_model.DesignTask.generation_request_id).where(
                    design_model.DesignTask.id == design_task_id
                )
            )
            if task_request_id is None:
                raise DesignTaskValidationError(
                    f"design task {design_task_id} does not exist"
                )
            research_repo = ResearchRepository(session)
            request = research_repo.get_generation_request(task_request_id)
            if request is None:
                raise DesignTaskValidationError(
                    f"generation_request {task_request_id} does not exist"
                )
            _lock_generation_request_or_busy(research_repo, request.id)
            task = session.scalars(
                sa.select(design_model.DesignTask)
                .where(design_model.DesignTask.id == design_task_id)
                .with_for_update()
            ).one()
            if task.status not in {"designed", "build_failed", "built"}:
                raise DesignTaskValidationError(
                    f"design task {task.id} is {task.status}; expected designed, build_failed, or built"
                )
            active = BuildAttemptsRepository(session).active_for_design_task(task.id)
            if active is not None:
                raise DesignTaskValidationError("active build attempt prevents design revision")
            if task.status == "built" and _has_released_production_membership(session, task.id):
                raise DesignTaskValidationError(
                    "released production design requires a new DesignTask version"
                )

            now = _utcnow()
            design_repo = ChallengeDesignRepository(session)
            design = design_repo.latest_design(task.id)
            if design is not None:
                design_row = session.get(challenge_model.ChallengeDesign, design.id)
                if design_row is not None:
                    design_row.status = "superseded"
                    design_row.updated_at = now
                attempt_row = session.get(challenge_model.DesignAttempt, design.design_attempt_id)
                if attempt_row is not None:
                    attempt_row.last_error = reason.strip()

            DesignEvidenceRepository(session).supersede_live_for_task(
                task.id,
                reason=reason,
            )
            reservation_repo = DesignProfileReservationRepository(session)
            if task.current_reservation_id is not None:
                reservation_repo.release_reservation(
                    task.current_reservation_id,
                    bump_ledger=True,
                )

            candidate = {
                "diversity_flags": {
                    "family": _semantic_value(task, "family"),
                    "sub_technique": _semantic_value(task, "sub_technique"),
                }
            }
            if task.category == "pwn":
                flags = dict(candidate["diversity_flags"])
                _pwn_semantic_for_reservation(
                    family=str(flags.get("family") or "other"),
                    raw_sub_technique=str(
                        flags.get("raw_sub_technique")
                        or flags.get("sub_technique")
                        or task.primary_technique
                    ),
                    flags=flags,
                )
                candidate["diversity_flags"] = flags
            task.status = "draft"
            task.plan_reviewed_at = None
            task.current_design_evidence_id = None
            task.updated_at = now
            session.flush()
            self._allocate_reservations(
                session=session,
                generation_request=request,
                tasks=[task],
                candidates=[candidate],
            )
            session.refresh(task)
            return _row_to_dto(task)

    def _allocate_reservations(
        self,
        *,
        session,
        generation_request: research_dto.GenerationRequest,
        tasks: Sequence[dto.DesignTask],
        candidates: Sequence[Mapping[str, Any]],
    ) -> int:
        reservation_repo = DesignProfileReservationRepository(session)
        policy = load_profile_policy(generation_request.category)
        ordered = sorted(
            zip(tasks, candidates, strict=True),
            key=lambda item: item[0].task_no,
        )
        taxonomy = taxonomy_for_category(generation_request.category)
        semantic_assignments = [
            _normalized_candidate_semantic(
                taxonomy,
                candidate,
                task.primary_technique,
                category=generation_request.category,
            )
            for task, candidate in ordered
        ]

        for attempt in range(2):
            ledger = reservation_repo.lock_ledger(
                generation_request.category,
                policy_version=policy.version,
            )
            existing = reservation_repo.list_active_occupancies(generation_request.category)
            try:
                with session.begin_nested():
                    ledger.ledger_version += 1
                    ledger.updated_at = _utcnow()
                    capacity = profile_capacity_check(
                        category=generation_request.category,
                        target_count=len(ordered),
                        semantic_assignments=semantic_assignments,
                        policy=policy,
                        existing=existing,
                    )
                    if not capacity.can_allocate:
                        raise DesignTaskValidationError(
                            capacity.diagnostics.get("code")
                            or "design_diversity_exhausted"
                        )
                    allocations = capacity.allocations

                    for (task, candidate), allocation in zip(ordered, allocations, strict=True):
                        signatures = canonical_profile_signatures(
                            allocation.profile,
                            category=generation_request.category,
                            policy_version=policy.version,
                        )
                        reservation = reservation_repo.reserve_task(
                            design_task_id=task.id,
                            generation_request_id=generation_request.id,
                            profile=allocation.profile.as_mapping(),
                            profile_signature=signatures.combined_profile_signature,
                            occupancy_scope=allocation.occupancy_scope,
                            exclusive_signature_key=allocation.exclusive_signature_key,
                            taxonomy_version=1,
                            policy_version=policy.version,
                            ledger_version=ledger.ledger_version,
                        )
                        reservation_repo.set_current_reservation(task.id, reservation.id)
                return len(ordered)
            except IntegrityError:
                session.expire_all()
                if attempt == 0:
                    continue
                raise
        return 0


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
    designable_findings = [
        finding for finding in findings if finding.kind in {"technique", "variant"}
    ]
    support_findings = [
        finding for finding in findings if finding.kind not in {"technique", "variant"}
    ]
    if not designable_findings:
        raise DesignTaskValidationError("insufficient_designable_capacity")
    capacity = profile_capacity_check(
        category=category,
        target_count=request.target_count,
        semantic_assignments=_semantic_assignments_for_findings(category, designable_findings),
    )
    if not capacity.can_allocate:
        raise DesignTaskValidationError(
            capacity.diagnostics.get("code") or "design_diversity_exhausted"
        )
    allocations = _allocate_primary_findings(
        designable_findings,
        target_count=request.target_count,
        category=category,
        technique_quota=profile.technique_quota,
        cooldown_window=profile.cooldown_window,
    )
    for index, difficulty in enumerate(difficulty_slots):
        task_no = index + 1
        allocation = allocations[index]
        primary = designable_findings[allocation.index]
        task_findings = _findings_for_task(
            difficulty,
            designable_findings,
            allocation.index,
        )
        secondaries = task_findings[1:]
        evidence_findings = [primary, *secondaries, *support_findings]
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
            "scenario": _scenario_for(category, difficulty, evidence_findings),
            "constraints": dict(runtime_constraints),
            "evidence_summary": _evidence_summary(run, request.topic, evidence_findings),
            "finding_ids": [f.id for f in evidence_findings],
            "diversity_flags": {
                **dict(allocation.diversity_flags),
                "advisory_mechanism_vocabulary": _advisory_mechanisms_for_category(category),
                "chosen_mechanism": None,
                "semantic_fingerprint": None,
                "diversity_rationale": (
                    "Model must choose the challenge mechanism from request, "
                    "research, and design evidence; code does not pre-assign an "
                    "exploit/template mechanism."
                ),
            },
        }
        if category == "pwn":
            flags = candidate["diversity_flags"]
            _pwn_semantic_for_reservation(
                family=str(flags.get("family") or "other"),
                raw_sub_technique=str(
                    flags.get("raw_sub_technique")
                    or flags.get("sub_technique")
                    or primary.label
                ),
                flags=flags,
            )
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
    flags = dict(candidate.get("diversity_flags") or {})
    flags["chosen_mechanism"] = enrichment.chosen_mechanism
    flags["semantic_fingerprint"] = enrichment.semantic_fingerprint
    flags["diversity_rationale"] = enrichment.diversity_rationale
    candidate["diversity_flags"] = flags
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
    flags = dict(diversity_flags)
    if category == "pwn":
        _pwn_semantic_for_reservation(
            family=str(
                flags.get("family")
                or resolve_family({"label": primary.label}, category=category)
            ),
            raw_sub_technique=str(
                flags.get("raw_sub_technique")
                or flags.get("sub_technique")
                or primary.label
            ),
            flags=flags,
        )
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
        "diversity_flags": flags,
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
        current_reservation_id=row.current_reservation_id,
        current_design_evidence_id=row.current_design_evidence_id,
        plan_reviewed_at=row.plan_reviewed_at,
    )


def _semantic_value(row: design_model.DesignTask, field: str) -> str:
    flags = row.diversity_flags or {}
    value = flags.get(field)
    if value:
        return str(value)
    if field == "family":
        return resolve_family({"label": row.primary_technique}, category=row.category)
    return resolve_sub_technique({"label": row.primary_technique})


def _has_released_production_membership(session, design_task_id: UUID) -> bool:
    # Corpus publication tables land later in this change. Until they exist in
    # the model layer, no row can mark a task as production-released.
    return False


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
    raw_sub_technique: str
    canonical_sub_technique: str


@dataclass(frozen=True)
class _FindingSemanticMetadata:
    family: str
    raw_sub_technique: str
    canonical_sub_technique: str
    canonicalization_source: str | None = None


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

    metadata = [_finding_semantic_metadata(category, finding) for finding in findings]
    family_counts: Counter[str] = Counter()
    used_subtechniques: set[str] = set()
    recent_families: list[str] = []
    allocations: list[_FindingAllocation] = []

    for task_index in range(target_count):
        ordered_indices = [(task_index + offset) % pool_size for offset in range(pool_size)]
        quota_candidates = [
            idx
            for idx in ordered_indices
            if family_counts[metadata[idx].family] < technique_quota
        ]
        family_quota_exceeded = False
        if not quota_candidates:
            quota_candidates = ordered_indices
            family_quota_exceeded = True

        cooldown_candidates = [
            idx
            for idx in quota_candidates
            if metadata[idx].family not in recent_families[-cooldown_window:]
        ] if cooldown_window > 0 else quota_candidates
        family_candidates = cooldown_candidates or quota_candidates

        unused_sub_candidates = [
            idx
            for idx in family_candidates
            if metadata[idx].canonical_sub_technique not in used_subtechniques
        ]
        if unused_sub_candidates:
            chosen = unused_sub_candidates[0]
            subtechnique_duplicate = False
        else:
            chosen = family_candidates[0]
            subtechnique_duplicate = True

        chosen_metadata = metadata[chosen]
        family = chosen_metadata.family
        raw_sub_technique = chosen_metadata.raw_sub_technique
        canonical_sub_technique = chosen_metadata.canonical_sub_technique
        canonicalization_source = chosen_metadata.canonicalization_source
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
                    "sub_technique": canonical_sub_technique,
                    "raw_sub_technique": raw_sub_technique,
                    "canonical_sub_technique": canonical_sub_technique,
                    **(
                        {"canonicalization_source": canonicalization_source}
                        if canonicalization_source
                        else {}
                    ),
                    "warnings": warnings,
                },
                avoid_techniques=frozenset(used_subtechniques),
                raw_sub_technique=raw_sub_technique,
                canonical_sub_technique=canonical_sub_technique,
            )
        )
        family_counts[family] += 1
        used_subtechniques.add(canonical_sub_technique)
        recent_families.append(family)
    return allocations


def _is_lock_not_available(exc: DBAPIError) -> bool:
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    if getattr(orig, "pgcode", None) == LOCK_NOT_AVAILABLE_SQLSTATE:
        return True
    if getattr(orig, "sqlstate", None) == LOCK_NOT_AVAILABLE_SQLSTATE:
        return True
    diag = getattr(orig, "diag", None)
    if diag is not None and getattr(diag, "sqlstate", None) == LOCK_NOT_AVAILABLE_SQLSTATE:
        return True
    return False


def _lock_generation_request_or_busy(
    research_repo: ResearchRepository,
    request_id: UUID,
) -> None:
    try:
        research_repo.lock_generation_request(request_id, nowait=True)
    except DBAPIError as exc:
        if _is_lock_not_available(exc):
            raise DesignTaskValidationError(
                "generation request is busy",
                code=GENERATION_REQUEST_BUSY_CODE,
            ) from exc
        raise


def _raise_persistence_failed(
    exc: DBAPIError,
    *,
    request_id: UUID,
    research_run_id: UUID | None,
    stage: str,
) -> None:
    event = {
        "design_task_insert": "insert_failed",
        "reservation_release": "reservation_failed",
        "reservation_allocation": "reservation_failed",
        "design_task_commit": "commit_failed",
    }.get(stage, "persistence_failed")
    _LOGGER.exception(
        "generate_design_tasks %s request_id=%s research_run_id=%s stage=%s exception_code=%s",
        event,
        request_id,
        research_run_id,
        stage,
        exc.__class__.__name__,
    )
    raise DesignTaskGenerationPersistenceError(
        request_id=request_id,
        stage=stage,
        message=(
            "design task generation failed while writing persistence state "
            f"at stage {stage}: {exc.__class__.__name__}"
        ),
    ) from exc


def _semantic_assignments_for_findings(
    category: str,
    findings: Sequence[research_dto.ResearchFinding],
) -> list[dict[str, str]]:
    taxonomy = taxonomy_for_category(category)
    if category == "pwn":
        return [
            _pwn_semantic_for_reservation(
                family=resolve_family(finding, category=category),
                raw_sub_technique=_raw_finding_label(finding),
            )
            for finding in findings
        ]
    return [
        _closed_semantic_assignment(
            taxonomy,
            family=resolve_family(finding, category=category),
            sub_technique=resolve_sub_technique(finding),
        )
        for finding in findings
    ]


def _normalized_candidate_semantic(
    taxonomy,
    candidate: Mapping[str, Any],
    primary_technique: str,
    *,
    category: str,
) -> dict[str, str]:
    flags = dict(candidate.get("diversity_flags") or {})
    if category == "pwn":
        semantic = _pwn_semantic_for_reservation(
            family=str(
                flags.get("family")
                or resolve_family({"label": primary_technique}, category=category)
            ),
            raw_sub_technique=str(
                flags.get("raw_sub_technique")
                or flags.get("sub_technique")
                or primary_technique
            ),
            flags=flags,
        )
        candidate_flags = candidate.get("diversity_flags")
        if isinstance(candidate_flags, dict):
            candidate_flags.update(flags)
        return semantic
    semantic = _closed_semantic_assignment(
        taxonomy,
        family=str(
            flags.get("family")
            or resolve_family({"label": primary_technique}, category=category)
        ),
        sub_technique=str(
            flags.get("sub_technique")
            or resolve_sub_technique({"label": primary_technique})
        ),
    )
    flags.update(semantic)
    candidate_flags = candidate.get("diversity_flags")
    if isinstance(candidate_flags, dict):
        candidate_flags.update(semantic)
    return semantic


def _closed_semantic_assignment(
    taxonomy,
    *,
    family: str,
    sub_technique: str,
) -> dict[str, str]:
    allowed_families = taxonomy.semantic.fields["family"]
    if family in allowed_families:
        closed_family = family
    else:
        inferred = resolve_family({"label": sub_technique}, category=taxonomy.category)
        closed_family = inferred if inferred in allowed_families else allowed_families[0]
    return normalize_semantic_assignment(
        taxonomy,
        {
            "family": closed_family,
            "sub_technique": sub_technique,
        },
    )


def _finding_semantic_metadata(
    category: str,
    finding: research_dto.ResearchFinding,
) -> _FindingSemanticMetadata:
    family = resolve_family(finding, category=category)
    raw_sub_technique = _raw_finding_label(finding)
    if category != "pwn":
        canonical_sub_technique = resolve_sub_technique(finding)
        return _FindingSemanticMetadata(
            family=family,
            raw_sub_technique=raw_sub_technique,
            canonical_sub_technique=canonical_sub_technique,
        )
    flags: dict[str, Any] = {}
    _pwn_semantic_for_reservation(
        family=family,
        raw_sub_technique=raw_sub_technique,
        flags=flags,
    )
    return _FindingSemanticMetadata(
        family=str(flags.get("family") or family),
        raw_sub_technique=str(flags.get("raw_sub_technique") or raw_sub_technique),
        canonical_sub_technique=str(
            flags.get("canonical_sub_technique")
            or flags.get("sub_technique")
            or resolve_sub_technique(finding)
        ),
        canonicalization_source=str(flags.get("canonicalization_source") or ""),
    )


def _raw_finding_label(finding: research_dto.ResearchFinding | Mapping[str, Any]) -> str:
    if isinstance(finding, Mapping):
        value = finding.get("label")
    else:
        value = getattr(finding, "label", None)
    return str(value or "").strip()


def _pwn_semantic_for_reservation(
    *,
    family: str,
    raw_sub_technique: str,
    flags: dict[str, Any] | None = None,
) -> dict[str, str]:
    canonicalization = canonicalize_pwn_semantic_assignment(
        {
            "family": family,
            "sub_technique": raw_sub_technique,
        }
    )
    if flags is not None:
        flags["raw_sub_technique"] = canonicalization.raw_sub_technique
        flags["canonical_sub_technique"] = canonicalization.canonical_sub_technique
        flags["canonicalization_source"] = canonicalization.canonicalization_source
        if canonicalization.semantic is not None:
            flags["family"] = canonicalization.semantic["family"]
            flags["sub_technique"] = canonicalization.semantic["sub_technique"]
    if canonicalization.semantic is None:
        return {
            "family": canonicalization.raw_family or "other",
            "sub_technique": canonicalization.raw_sub_technique or "unknown",
        }
    return dict(canonicalization.semantic)


# Per-category advisory mechanism vocabulary. These examples help the model talk
# about diversity, but code must not assign one as a binding exploit mechanism.
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


def _advisory_mechanisms_for_category(category: str) -> tuple[str, ...]:
    configured = _generation_profile_category(category).get("advisory_mechanisms")
    if not isinstance(configured, list):
        configured = _generation_profile_category(category).get("mechanisms")
    if isinstance(configured, list):
        cleaned = tuple(m for m in configured if isinstance(m, str) and m.strip())
        if cleaned:
            return cleaned
    return _DEFAULT_MECHANISMS.get(category, ("generic_gate",))


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
        return category_profile_config(category)
    except Exception as exc:  # noqa: BLE001 - missing/malformed profile should not block planning
        _LOGGER.warning("could not read generation profile: %s", exc)
        return {}


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
    """Return a short player-facing challenge name, never a task label."""
    source = finding.label.strip() or topic.strip() or "challenge"
    words = re.findall(r"[A-Za-z]+", source)
    if not words:
        return f"Chal{task_no}"
    parts: list[str] = []
    for word in words:
        candidate = "".join(parts + [word.title()])
        if len(candidate) > 15:
            break
        parts.append(word.title())
    title = "".join(parts) or words[0][:15].title()
    return title[:15]


def _challenge_id(category: str, request_id: UUID, task_no: int) -> str:
    return f"{category}-{request_id.hex[:8]}-{task_no:04d}"


def _port_for(category: str, task_no: int) -> int | None:
    if category in {"web", "pwn"}:
        return DEFAULT_PORT_BASE + task_no
    return None
