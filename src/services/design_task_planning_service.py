"""Convert a researched generation request into design task rows.

Section 4 of the ``add-design-task-planning`` change. The service is
the only place that knows how to assemble candidate task rows from a
generation request + its completed research run + findings/sources.
The Hermes-driven requirement-planning agent is not part of this
change; this service uses a deterministic adapter that picks fields
straight from the request and round-robins findings across the
``target_count`` tasks so the unit/integration tests are reproducible.

The service deliberately does NOT render any prompt text. Prompt
rendering happens at design-execution time in a later change.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from uuid import UUID

from domain import design_tasks as dto
from domain import research as research_dto
from domain.design_task_validators import DesignTaskValidationError
from persistence.repositories import DesignTaskRepository, ResearchRepository
from persistence.session import SessionFactory, transaction

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

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        # Same shape as ResearchJobService: the service owns one short
        # transaction per public method and never holds a long-lived
        # session.
        self.session_factory = session_factory

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
            if len(findings) < math.ceil(request.target_count * 0.5):
                raise DesignTaskValidationError("insufficient_findings")
            if not findings or not sources:
                raise DesignTaskValidationError(
                    "completed research run has no sources or findings; "
                    "cannot generate design tasks"
                )

            candidates = _plan_candidates(request, latest, findings)
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


def _plan_candidates(
    request: research_dto.GenerationRequest,
    run: research_dto.ResearchRun,
    findings: Sequence[research_dto.ResearchFinding],
) -> list[dict[str, Any]]:
    """Deterministic planner: 1 candidate per target slot, findings round-robin.

    The output already conforms to
    :func:`domain.design_task_validators.validate_candidate`. The
    planning service hands these to the repository, which re-validates
    so a future Hermes-backed planner cannot silently break the same
    contract.
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
        finding = findings[index % len(findings)]
        candidate: dict[str, Any] = {
            "task_no": task_no,
            "challenge_id": _challenge_id(category, request.id, task_no),
            "title": _title(request.topic, finding, task_no),
            "category": category,
            "difficulty": difficulty,
            "primary_technique": finding.label,
            "learning_objective": (
                f"Reproduce {finding.label} on a {category} target "
                f"derived from research run {run.attempt}."
            ),
            "points": DEFAULT_POINTS.get(difficulty, 100),
            "port": _port_for(category, task_no),
            "scenario": finding.summary,
            "constraints": dict(runtime_constraints),
            "evidence_summary": (
                f"{finding.kind} cited from research run {run.id} on topic "
                f"{request.topic!r}."
            ),
            "finding_ids": [finding.id],
        }
        candidates.append(candidate)
    return candidates


def _title(topic: str, finding: research_dto.ResearchFinding, task_no: int) -> str:
    base = topic.strip() or finding.label
    return f"{base} — task {task_no}"


def _challenge_id(category: str, request_id: UUID, task_no: int) -> str:
    return f"{category}-{request_id.hex[:8]}-{task_no:04d}"


def _port_for(category: str, task_no: int) -> int | None:
    if category in {"web", "pwn"}:
        return DEFAULT_PORT_BASE + task_no
    return None
