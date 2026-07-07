"""Service orchestration for one structured challenge-design attempt."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from core.paths import ProjectPaths
from domain import challenge_designs as design_dto
from domain import design_profile_reservations as reservation_dto
from domain import design_tasks as task_dto
from domain import research as research_dto
from domain.challenge_design_validators import (
    ChallengeDesignValidationError,
    normalize_design_payload_for_task,
    parse_design_output,
    run_quality_gate,
    validate_design_payload,
)
from persistence.models import design_tasks as dt_model
from persistence.repositories import (
    ChallengeDesignRepository,
    DesignEvidenceRepository,
    DesignProfileReservationRepository,
    DesignTaskRepository,
    ResearchRepository,
)
from persistence.session import SessionFactory, transaction
from services.design_agent_executor import (
    DesignChallengeExecutor,
    PROVIDER_RATE_LIMITED_ERROR,
    last_error_for_exit_code,
)
from services.design_governance import (
    DesignGovernanceError,
    DesignLedgerSnapshot,
    build_design_ledger_snapshot,
    detect_conflicting_ledger_advance,
    validate_design_evidence_output,
)
from services.design_prompt import (
    DesignPromptContext,
    build_design_prompt,
    load_design_prompt_context,
)

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
    reservation: reservation_dto.DesignProfileReservation | None = None
    ledger_snapshot: DesignLedgerSnapshot | None = None
    previous_design_seed: Mapping[str, Any] | None = None


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
        workspace = self.paths.design_executions / str(attempt.id)
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
                previous_design_seed_path=_stage_previous_design_seed(
                    workspace, started.previous_design_seed
                ),
                prior_designs=started.prior_designs,
                reservation=(
                    _reservation_prompt_mapping(started.reservation)
                    if started.reservation is not None
                    else None
                ),
                ledger_snapshot=(
                    started.ledger_snapshot.as_prompt_mapping()
                    if started.ledger_snapshot is not None
                    else None
                ),
            )
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(prompt_text, encoding="utf-8")
            with transaction(factory=self.session_factory) as session:
                ChallengeDesignRepository(session).record_prompt_path(
                    attempt.id,
                    attempt.claim_token,
                    prompt_rel,
                )

            root_output_snapshot = _project_root_output_snapshot(self.paths.root)
            stdout, exit_code, _duration_s = self.executor.execute(
                prompt_text,
                attempt.profile_name_used,
                self.timeout_seconds,
                log_path,
                workspace,
            )
            root_output_leaks = _project_root_output_leaks(
                self.paths.root,
                root_output_snapshot,
            )
            if root_output_leaks:
                leak_list = ", ".join(root_output_leaks[:5])
                if len(root_output_leaks) > 5:
                    leak_list += f", ... (+{len(root_output_leaks) - 5} more)"
                return self._fail_attempt(
                    attempt,
                    log_rel,
                    "Hermes design wrote output outside the design workspace under "
                    f"project root: {leak_list}",
                    started.max_attempts,
                )
            log_text = _read_text_if_small(log_path)
            exit_error = last_error_for_exit_code(
                exit_code,
                "\n".join((stdout, log_text)),
            )
            if exit_error is not None:
                return self._fail_attempt(
                    attempt,
                    log_rel,
                    exit_error,
                    started.max_attempts,
                    retryable=exit_error == PROVIDER_RATE_LIMITED_ERROR,
                )

            parsed = _parse_design_output_with_workspace_fallback(stdout, workspace)
            parsed = normalize_design_payload_for_task(parsed, started.design_task)
            _write_design_snapshot(workspace, parsed)
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
                if started.reservation is not None and started.ledger_snapshot is not None:
                    reservation_repo = DesignProfileReservationRepository(session)
                    evidence_repo = DesignEvidenceRepository(session)
                    evidence_payload = validate_design_evidence_output(
                        challenge=validated.challenge,
                        design_task=started.design_task,
                        reservation=started.reservation,
                        findings=started.findings,
                        ledger_snapshot=started.ledger_snapshot,
                    )
                    if detect_conflicting_ledger_advance(
                        evidence_repo=evidence_repo,
                        reservation_repo=reservation_repo,
                        design_task=started.design_task,
                        reservation=started.reservation,
                        consumed_snapshot=started.ledger_snapshot,
                    ):
                        raise DesignGovernanceError("stale_design_ledger")
                    reservation_repo.commit_reservation(started.reservation.id)
                    evidence_repo.create_live(
                        design_task_id=design.design_task_id,
                        challenge_design_id=design.id,
                        research_finding_ids=evidence_payload["research_finding_ids"],
                        profile=evidence_payload["profile"],
                        profile_signature=evidence_payload["profile_signature"],
                        distinctness_claim=evidence_payload["distinctness_claim"],
                        compared_challenge_ids=evidence_payload["compared_challenge_ids"],
                        evidence={
                            **dict(evidence_payload["evidence"]),
                            "challenge_id": started.design_task.challenge_id,
                        },
                        build_contract=evidence_payload["build_contract"],
                        ledger_version=evidence_payload["ledger_version"],
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
        except DesignGovernanceError as exc:
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

            attempts = design_repo.list_attempts(design_task_id)
            latest_attempt = attempts[-1] if attempts else None
            consumed_attempts = [
                item for item in attempts if item.last_error != PROVIDER_RATE_LIMITED_ERROR
            ]
            if len(consumed_attempts) >= request.max_attempts:
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
            previous_design_seed = _load_previous_design_seed(
                self.paths,
                design_repo=design_repo,
                design_task_id=design_task_id,
                latest_attempt=latest_attempt,
            )
            reservation = None
            ledger_snapshot = None
            if design_task.current_reservation_id is not None:
                reservation_repo = DesignProfileReservationRepository(session)
                reservation = reservation_repo.get(design_task.current_reservation_id)
                if reservation is None:
                    raise ChallengeDesignConflictError(
                        f"reservation {design_task.current_reservation_id} does not exist"
                    )
                if reservation.state != "reserved":
                    raise ChallengeDesignConflictError(
                        f"reservation {reservation.id} is {reservation.state}, expected reserved"
                    )
                ledger_snapshot = build_design_ledger_snapshot(
                    evidence_repo=DesignEvidenceRepository(session),
                    reservation_repo=reservation_repo,
                    design_task=design_task,
                    reservation=reservation,
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
                reservation=reservation,
                ledger_snapshot=ledger_snapshot,
                previous_design_seed=previous_design_seed,
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
                    "chosen_mechanism": flags.get("chosen_mechanism"),
                    "semantic_fingerprint": flags.get("semantic_fingerprint"),
                    "diversity_rationale": flags.get("diversity_rationale"),
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
        *,
        retryable: bool = False,
    ) -> ChallengeDesignServiceResult:
        with transaction(factory=self.session_factory) as session:
            design_repo = ChallengeDesignRepository(session)
            failed = design_repo.fail_attempt(
                attempt.id,
                attempt.claim_token,
                log_path,
                last_error,
                max(attempt.attempt + 1, max_attempts) if retryable else max_attempts,
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


def _load_previous_design_seed(
    paths: ProjectPaths,
    *,
    design_repo: ChallengeDesignRepository,
    design_task_id: UUID,
    latest_attempt: design_dto.DesignAttempt | None,
) -> Mapping[str, Any] | None:
    latest_design = design_repo.latest_design(design_task_id)
    if latest_design is not None and latest_design.payload:
        return dict(latest_design.payload)
    if latest_attempt is None:
        return None
    snapshot_path = (
        paths.design_executions
        / str(latest_attempt.id)
        / "state"
        / "last_design_draft.json"
    )
    return _read_design_snapshot(snapshot_path)


def _stage_previous_design_seed(
    workspace: Path,
    seed: Mapping[str, Any] | None,
) -> str | None:
    if seed is None:
        return None
    state_dir = workspace / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "previous_design.json"
    _write_json_file(path, seed)
    return "./state/previous_design.json"


def _write_design_snapshot(workspace: Path, payload: Mapping[str, Any]) -> None:
    state_dir = workspace / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json_file(state_dir / "last_design_draft.json", payload)


def _read_design_snapshot(path: Path) -> Mapping[str, Any] | None:
    try:
        if not path.is_file():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(loaded, dict):
        return loaded
    return None


def _write_json_file(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return


def _parse_design_output_with_workspace_fallback(
    stdout: str,
    workspace: Path,
) -> dict[str, Any]:
    try:
        return parse_design_output(stdout)
    except ChallengeDesignValidationError as original:
        recovered = _recover_design_output_from_workspace(workspace)
        if recovered is not None:
            return recovered
        raise original


def _recover_design_output_from_workspace(workspace: Path) -> dict[str, Any] | None:
    if not workspace.exists():
        return None
    for path in _iter_workspace_output_candidates(workspace):
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            return parse_design_output(text)
        except ChallengeDesignValidationError:
            continue
    return None


def _iter_workspace_output_candidates(workspace: Path) -> list[Path]:
    suffixes = {".json", ".txt", ".md", ".out"}
    priority = [
        workspace / "state" / "design_output.json",
        workspace / "design_output.json",
    ]
    candidates: list[Path] = []
    seen: set[Path] = set()
    priority_count = 0
    for path in priority:
        if path.is_file():
            candidates.append(path)
            seen.add(path.resolve())
            priority_count += 1
    try:
        iterator = workspace.rglob("*")
        for path in iterator:
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in seen:
                continue
            if path.suffix.lower() in suffixes:
                candidates.append(path)
    except OSError:
        return candidates
    head = candidates[:priority_count]
    tail = sorted(
        candidates[priority_count:],
        key=lambda path: path.relative_to(workspace).as_posix(),
    )
    return head + tail


def _read_text_if_small(path: Path, *, limit: int = 1_000_000) -> str:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _reservation_prompt_mapping(
    reservation: reservation_dto.DesignProfileReservation | None,
) -> dict[str, Any] | None:
    if reservation is None:
        return None
    return {
        "id": str(reservation.id),
        "reservation_version": reservation.reservation_version,
        "reserved_profile": dict(reservation.profile),
        "profile_signature": reservation.profile_signature,
        "taxonomy_version": reservation.taxonomy_version,
        "policy_version": reservation.policy_version,
        "ledger_version": reservation.ledger_version,
    }


def _validation_notes(base_notes: str, quality_notes: Sequence[str]) -> str:
    if not quality_notes:
        return base_notes
    lines = [base_notes, "", "Quality gate notes:"]
    lines.extend(f"- {note}" for note in quality_notes)
    return "\n".join(lines)


_PROJECT_ROOT_LEAK_DIRS = ("output", "challenges", ".design_output")
_PROJECT_ROOT_LEAK_FILE_PREFIXES = (
    "challenge",
    "design",
    "re-",
    "web-",
    "pwn-",
)


def _project_root_output_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in _iter_project_root_output_candidates(root):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        snapshot[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _project_root_output_leaks(
    root: Path,
    before: Mapping[str, tuple[int, int]],
) -> list[str]:
    leaks: list[str] = []
    for path in _iter_project_root_output_candidates(root):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        if before.get(relative) != (stat.st_size, stat.st_mtime_ns):
            leaks.append(relative)
    return sorted(leaks)


def _iter_project_root_output_candidates(root: Path):
    for dirname in _PROJECT_ROOT_LEAK_DIRS:
        directory = root / dirname
        if directory.is_dir() and not directory.is_symlink():
            yield from directory.rglob("*")
    try:
        direct_children = list(root.iterdir())
    except OSError:
        return
    for path in direct_children:
        if not path.is_file() or path.suffix != ".json":
            continue
        if path.name.startswith(_PROJECT_ROOT_LEAK_FILE_PREFIXES):
            yield path
