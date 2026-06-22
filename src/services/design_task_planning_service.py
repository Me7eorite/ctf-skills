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
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from uuid import UUID

from domain import design_tasks as dto
from domain import research as research_dto
from domain.design_task_validators import DesignTaskValidationError
from domain.research_validators import (
    _quality_ratio,
    _quality_soft_pass_slack,
)
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
    for index, difficulty in enumerate(difficulty_slots):
        task_no = index + 1
        task_findings = _findings_for_task(difficulty, findings, index)
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


def _findings_for_task(
    difficulty: str,
    findings: Sequence[research_dto.ResearchFinding],
    index: int,
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
    return [findings[(index + offset) % pool_size] for offset in range(need)]


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
