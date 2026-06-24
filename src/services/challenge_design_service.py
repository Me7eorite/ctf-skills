"""Service orchestration for one structured challenge-design attempt."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa

from core.paths import ProjectPaths
from domain import challenge_designs as design_dto
from domain import design_tasks as task_dto
from domain import research as research_dto
from domain.challenge_design_validators import (
    ChallengeDesignValidationError,
    parse_design_output,
    run_quality_gate,
    validate_design_payload,
)
from persistence.models import design_tasks as dt_model
from persistence.repositories import ChallengeDesignRepository, DesignTaskRepository, ResearchRepository
from persistence.session import SessionFactory, transaction
from services.design_agent_executor import DesignChallengeExecutor, last_error_for_exit_code
from services.design_prompt import DesignPromptContext, build_design_prompt, load_design_prompt_context

DESIGN_BINDING_ROLE = "design"
DEFAULT_PROFILE_NAME = "default"
DEFAULT_DESIGN_TIMEOUT_SECONDS = 600


class ChallengeDesignServiceError(ValueError):
    """Base class for design service errors."""


class ChallengeDesignNotFoundError(ChallengeDesignServiceError):
    """Raised when the requested design task does not exist."""


class ChallengeDesignConflictError(ChallengeDesignServiceError):
    """Raised when an attempt cannot be started for the current task state."""


@dataclass(frozen=True)
class ChallengeDesignServiceResult:
    design_task_id: UUID
    attempt_id: UUID
    design_task_status: str
    attempt_status: str
    challenge_design: design_dto.ChallengeDesign | None
    error: str | None


@dataclass(frozen=True)
class _AttemptStart:
    attempt: design_dto.DesignAttempt
    design_task: task_dto.DesignTask
    generation_request: research_dto.GenerationRequest
    findings: Sequence[research_dto.ResearchFinding]
    sources: Sequence[research_dto.ResearchSource]
    max_attempts: int
    previous_error: str | None
    # Sequential design memory: digests of already-designed sibling tasks in the
    # same batch, so this design can plan AGAINST them and avoid collapsing into
    # the same concept / mechanism / solution shape.
    prior_designs: Sequence[Mapping[str, Any]] = ()


PromptContextLoader = Callable[[ProjectPaths], DesignPromptContext]


class ChallengeDesignService:
    """Run the synchronous design-attempt lifecycle for one queued task."""

    def __init__(
        self,
        *,
        paths: ProjectPaths | None = None,
        session_factory: SessionFactory | None = None,
        executor: DesignChallengeExecutor | None = None,
        timeout_seconds: int = DEFAULT_DESIGN_TIMEOUT_SECONDS,
        prompt_context_loader: PromptContextLoader = load_design_prompt_context,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.paths = paths or ProjectPaths.discover()
        self.session_factory = session_factory
        self.executor = executor or DesignChallengeExecutor(self.paths)
        self.timeout_seconds = timeout_seconds
        self.prompt_context_loader = prompt_context_loader

    def design_for_task(
        self,
        design_task_id: UUID,
        caller: str,
    ) -> ChallengeDesignServiceResult:
        """Design one queued task and persist either a draft design or a failure."""
        started = self._start_attempt(design_task_id, caller)
        attempt = started.attempt
        prompt_path = self.paths.design_prompts / f"{attempt.id}.md"
        log_path = self.paths.design_logs / f"{attempt.id}.log"
        prompt_rel = self._relative_path(prompt_path)
        log_rel = self._relative_path(log_path)

        try:
            context = self.prompt_context_loader(self.paths)
            prompt_text = build_design_prompt(
                context,
                started.design_task,
                started.generation_request,
                started.findings,
                started.sources,
                previous_error=started.previous_error,
                prior_designs=started.prior_designs,
            )
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(prompt_text, encoding="utf-8")
            with transaction(factory=self.session_factory) as session:
                ChallengeDesignRepository(session).record_prompt_path(
                    attempt.id,
                    attempt.claim_token,
                    prompt_rel,
                )

            stdout, exit_code, _duration_s = self.executor.execute(
                prompt_text,
                attempt.profile_name_used,
                self.timeout_seconds,
                log_path,
            )
            exit_error = last_error_for_exit_code(exit_code)
            if exit_error is not None:
                return self._fail_attempt(attempt, log_rel, exit_error, started.max_attempts)

            parsed = parse_design_output(stdout)
            validated = validate_design_payload(parsed, started.design_task)
            quality_gate_passed, quality_notes = run_quality_gate(validated.payload)
            validation_notes = _validation_notes(
                validated.validation_notes,
                quality_notes,
            )

            with transaction(factory=self.session_factory) as session:
                design_repo = ChallengeDesignRepository(session)
                design = design_repo.complete_attempt(
                    attempt.id,
                    attempt.claim_token,
                    log_rel,
                    validated.payload,
                    validated.summary,
                    validated.flag_format,
                    validation_notes,
                    quality_gate_passed,
                )
                task = DesignTaskRepository(session).get_design_task(design.design_task_id)
                if task is None:
                    raise ChallengeDesignNotFoundError(
                        f"design task {design.design_task_id} does not exist"
                    )
                self._record_ledger(started.design_task, validated.payload)
                return ChallengeDesignServiceResult(
                    design_task_id=design.design_task_id,
                    attempt_id=attempt.id,
                    design_task_status=task.status,
                    attempt_status="completed",
                    challenge_design=design,
                    error=None,
                )
        except ChallengeDesignValidationError as exc:
            return self._fail_attempt(attempt, log_rel, str(exc), started.max_attempts)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            return self._fail_attempt(attempt, log_rel, error, started.max_attempts)

    def _start_attempt(self, design_task_id: UUID, caller: str) -> _AttemptStart:
        if not caller:
            raise ChallengeDesignConflictError("caller is required")
        with transaction(factory=self.session_factory) as session:
            task_row = session.scalars(
                sa.select(dt_model.DesignTask)
                .where(dt_model.DesignTask.id == design_task_id)
                .with_for_update()
            ).one_or_none()
            if task_row is None:
                raise ChallengeDesignNotFoundError(
                    f"design task {design_task_id} does not exist"
                )
            if task_row.status != "queued":
                raise ChallengeDesignConflictError(
                    f"design task {design_task_id} is {task_row.status}, expected queued"
                )

            research_repo = ResearchRepository(session)
            task_repo = DesignTaskRepository(session)
            design_repo = ChallengeDesignRepository(session)
            design_task = task_repo.get_design_task(design_task_id)
            if design_task is None:
                raise ChallengeDesignNotFoundError(
                    f"design task {design_task_id} does not exist"
                )
            request = research_repo.get_generation_request(design_task.generation_request_id)
            if request is None:
                raise ChallengeDesignConflictError(
                    f"generation request {design_task.generation_request_id} does not exist"
                )

            latest_attempt = design_repo.latest_attempt(design_task_id)
            if latest_attempt is not None and latest_attempt.attempt >= request.max_attempts:
                raise ChallengeDesignConflictError(
                    f"design task {design_task_id} exhausted {request.max_attempts} attempt(s)"
                )

            profile_name = _resolve_profile_name(research_repo)
            attempt_no = 1 if latest_attempt is None else latest_attempt.attempt + 1
            attempt = design_repo.create_attempt(
                design_task_id,
                attempt_no,
                caller,
                profile_name,
            )
            findings = _filter_task_findings(
                research_repo.list_findings(design_task.research_run_id),
                design_task.finding_ids,
            )
            sources = research_repo.list_sources(design_task.research_run_id)
            prior_designs = _collect_prior_designs(
                task_repo, design_repo, design_task
            )
            return _AttemptStart(
                attempt=attempt,
                design_task=design_task,
                generation_request=request,
                findings=findings,
                sources=sources,
                max_attempts=request.max_attempts,
                previous_error=(
                    latest_attempt.last_error if latest_attempt is not None else None
                ),
                prior_designs=prior_designs,
            )

    def _record_ledger(
        self, design_task: task_dto.DesignTask, payload: Mapping[str, Any]
    ) -> None:
        """Append this design's digest to the cross-batch experience ledger.

        Best-effort: the ledger is a planning optimization, never a correctness
        dependency, so any failure here must not fail an otherwise-valid design.
        """
        try:
            from domain.design.collapse import challenge_fingerprint
            from services.design_ledger import append_design

            challenges = (payload or {}).get("challenges") or []
            challenge = challenges[0] if challenges else {}
            flags = design_task.diversity_flags or {}
            digest = _design_digest(challenge, design_task)
            append_design(
                self.paths,
                {
                    "generation_request_id": str(design_task.generation_request_id),
                    "fingerprint": challenge_fingerprint(
                        {**challenge, "diversity_flags": flags}
                    ),
                    "core_mechanism": flags.get("core_mechanism"),
                    **digest,
                },
            )
        except Exception:  # noqa: BLE001 — ledger is non-critical
            return

    def _fail_attempt(
        self,
        attempt: design_dto.DesignAttempt,
        log_path: str,
        last_error: str,
        max_attempts: int,
    ) -> ChallengeDesignServiceResult:
        with transaction(factory=self.session_factory) as session:
            design_repo = ChallengeDesignRepository(session)
            failed = design_repo.fail_attempt(
                attempt.id,
                attempt.claim_token,
                log_path,
                last_error,
                max_attempts,
            )
            task = DesignTaskRepository(session).get_design_task(failed.design_task_id)
            if task is None:
                raise ChallengeDesignNotFoundError(
                    f"design task {failed.design_task_id} does not exist"
                )
            return ChallengeDesignServiceResult(
                design_task_id=failed.design_task_id,
                attempt_id=failed.id,
                design_task_status=task.status,
                attempt_status=failed.status,
                challenge_design=None,
                error=last_error,
            )

    def _relative_path(self, path: Path) -> str:
        relative = path.resolve().relative_to(self.paths.root.resolve())
        return relative.as_posix()


def _resolve_profile_name(repo: ResearchRepository) -> str:
    binding = repo.get_binding(DESIGN_BINDING_ROLE)
    if binding is None or binding.status != "enabled":
        return DEFAULT_PROFILE_NAME
    return binding.profile_name


_PRIOR_STATUSES = ("designed", "building", "built")


def _collect_prior_designs(
    task_repo: DesignTaskRepository,
    design_repo: ChallengeDesignRepository,
    design_task: task_dto.DesignTask,
) -> list[dict[str, Any]]:
    """Digest already-designed sibling tasks of the same batch (ordered).

    Sequential design memory: when designing task N, summarize tasks that were
    already designed in the same generation request so the prompt can steer the
    new design AWAY from their concepts/mechanisms/solution shapes — the core
    of the anti-collapse "plan stage".
    """
    siblings = task_repo.list_design_tasks(design_task.generation_request_id)
    digests: list[dict[str, Any]] = []
    for sib in siblings:
        if sib.id == design_task.id or sib.status not in _PRIOR_STATUSES:
            continue
        design = design_repo.latest_design(sib.id)
        if design is None:
            continue
        challenges = (design.payload or {}).get("challenges") or []
        challenge = challenges[0] if challenges else {}
        digests.append(_design_digest(challenge, sib))
    return digests


def _design_digest(
    challenge: Mapping[str, Any], sib: task_dto.DesignTask
) -> dict[str, Any]:
    """Compact, collapse-relevant summary of one prior design."""
    asset_flow = challenge.get("asset_flow")
    flow_shape = []
    if isinstance(asset_flow, list):
        for stage in asset_flow:
            if isinstance(stage, Mapping):
                produced = stage.get("produced_asset_or_capability")
                if isinstance(produced, str) and produced.strip():
                    flow_shape.append(produced.strip())
    return {
        "id": challenge.get("id") or sib.challenge_id,
        "category": challenge.get("category") or sib.category,
        "difficulty": challenge.get("difficulty") or sib.difficulty,
        "primary_technique": challenge.get("primary_technique")
        or sib.primary_technique,
        "techniques": [
            t for t in (challenge.get("techniques") or []) if isinstance(t, str)
        ],
        "asset_flow_shape": flow_shape,
        "unintended_solutions": [
            s
            for s in (challenge.get("unintended_solutions") or [])
            if isinstance(s, str)
        ],
    }


def _filter_task_findings(
    findings: Sequence[research_dto.ResearchFinding],
    finding_ids: Sequence[UUID],
) -> list[research_dto.ResearchFinding]:
    if not finding_ids:
        return list(findings)
    wanted = set(finding_ids)
    return [finding for finding in findings if finding.id in wanted]


def _validation_notes(base_notes: str, quality_notes: Sequence[str]) -> str:
    if not quality_notes:
        return base_notes
    lines = [base_notes, "", "Quality gate notes:"]
    lines.extend(f"- {note}" for note in quality_notes)
    return "\n".join(lines)
