"""Automatic bounded iteration for failed build attempts."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from core.clock import beijing_now_isoformat
from core.jsonio import read_json, write_json
from core.paths import ProjectPaths
from core.state import ProgressStore
from domain.build_attempt_auto_repair import AutoRepairResult
from domain.validation_failure_governance import latest_failed_validation
from domain.validation_repair_policy import (
    ValidationRepairPolicy,
    no_progress_repair_blocked,
    policy_for_validation_failure,
    validation_failure_fingerprints,
    validation_repair_progress_fingerprints,
)
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.repositories import BuildAttemptsRepository
from persistence.session import SessionFactory, transaction
from services.build_attempt_auto_repair_service import auto_repair_challenge
from services.build_attempt_repair_service import (
    BuildAttemptRepairError,
    BuildAttemptRepairService,
    _attempt_payload,
    _challenge_directory,
    _challenge_file_fingerprint,
    _challenge_ids,
)
from services.build_attempt_revalidation_service import (
    BuildAttemptRevalidationError,
    BuildAttemptRevalidationService,
)
from services.build_orchestration_service import (
    BuildOrchestrationError,
    BuildOrchestrationService,
)

LOG = logging.getLogger(__name__)
AUTO_ITERATION_WORKER = "auto-iteration"
TERMINAL_AUTO_STATUSES = {"succeeded", "blocked", "exhausted", "retry_queued"}


@dataclass(frozen=True)
class AutoIterationBudget:
    max_iterations: int = 5
    max_deterministic_repairs: int = 2
    max_hermes_repairs: int = 2
    max_retry_attempts: int = 1


@dataclass(frozen=True)
class AutoIterationAttemptResult:
    attempt_id: UUID
    status: str
    iteration_count: int
    selected_route: str | None = None
    reason: str | None = None
    next_attempt_id: UUID | None = None


@dataclass(frozen=True)
class AutoIterationBatchResult:
    processed: int
    outcomes: list[AutoIterationAttemptResult]


@dataclass(frozen=True)
class _AttemptSnapshot:
    id: UUID
    design_task_id: UUID
    status: str
    shard_basename: str
    resulting_challenge_dir: str | None
    category: str | None
    challenge_id: str | None
    current_execution_id: UUID | None
    latest_execution_id: UUID | None
    is_latest: bool
    has_active_attempt: bool
    task_status: str | None


class BuildAttemptAutoIterationService:
    """Drive failed attempts through policy-selected repair/revalidate rounds."""

    def __init__(
        self,
        *,
        paths: ProjectPaths | None = None,
        progress: ProgressStore,
        session_factory: SessionFactory | None = None,
        budget: AutoIterationBudget | None = None,
        deterministic_repair: Callable[..., AutoRepairResult] = auto_repair_challenge,
        revalidation_service: BuildAttemptRevalidationService | None = None,
        repair_service: BuildAttemptRepairService | None = None,
        orchestration_service: BuildOrchestrationService | None = None,
    ) -> None:
        self.paths = paths or ProjectPaths.discover()
        self.progress = progress
        self.session_factory = session_factory or SessionFactory()
        self.budget = budget or AutoIterationBudget()
        self.deterministic_repair = deterministic_repair
        self.revalidation_service = revalidation_service or BuildAttemptRevalidationService(
            paths=self.paths,
            progress=progress,
            session_factory=self.session_factory,
            worker=AUTO_ITERATION_WORKER,
            use_advisory_lock=False,
        )
        self.repair_service = repair_service or BuildAttemptRepairService(
            paths=self.paths,
            progress=progress,
            session_factory=self.session_factory,
            use_advisory_lock=False,
        )
        self.orchestration_service = orchestration_service or BuildOrchestrationService(
            paths=self.paths,
            session_factory=self.session_factory,
        )

    def run_once(self, *, limit: int = 20) -> AutoIterationBatchResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        attempt_ids = self._latest_failed_attempt_ids(limit)
        outcomes: list[AutoIterationAttemptResult] = []
        for attempt_id in attempt_ids:
            outcomes.append(self.iterate_attempt(attempt_id))
        return AutoIterationBatchResult(processed=len(outcomes), outcomes=outcomes)

    def run_loop(self, *, limit: int = 20, poll_seconds: float = 30.0) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        while True:
            result = self.run_once(limit=limit)
            LOG.info("auto iteration tick processed=%s", result.processed)
            time.sleep(poll_seconds)

    def iterate_attempt(self, attempt_id: UUID) -> AutoIterationAttemptResult:
        with self._attempt_advisory_lock(attempt_id) as acquired:
            if not acquired:
                return AutoIterationAttemptResult(
                    attempt_id=attempt_id,
                    status="busy",
                    iteration_count=self._state_counts(attempt_id).get("iteration_count", 0),
                    reason="advisory_lock_busy",
                )
            return self._iterate_attempt_locked(attempt_id)

    def _iterate_attempt_locked(self, attempt_id: UUID) -> AutoIterationAttemptResult:
        state = self._load_state(attempt_id)
        if state.get("status") in TERMINAL_AUTO_STATUSES:
            return AutoIterationAttemptResult(
                attempt_id=attempt_id,
                status="skipped",
                iteration_count=int(state.get("iteration_count") or 0),
                reason=f"auto_iteration_already_{state.get('status')}",
            )

        snapshot = self._attempt_snapshot(attempt_id)
        skip_reason = self._skip_reason(snapshot)
        if skip_reason is not None:
            self._record_state_event(
                attempt_id,
                state,
                event_type="auto_iteration_skipped",
                selected_route="skipped",
                action_result=skip_reason,
                next_status=snapshot.status if snapshot else "missing",
            )
            return AutoIterationAttemptResult(
                attempt_id=attempt_id,
                status="skipped",
                iteration_count=int(state.get("iteration_count") or 0),
                reason=skip_reason,
            )

        self._record_state_event(
            attempt_id,
            state,
            event_type="auto_iteration_started",
            selected_route="start",
            selected_reason="failed attempt selected for automatic iteration",
            next_status=snapshot.status,
        )
        self._record_progress(
            snapshot.shard_basename,
            "running",
            {"event": "auto_iteration_started", "iteration_count": state.get("iteration_count", 0)},
        )

        while int(state.get("iteration_count") or 0) < self.budget.max_iterations:
            snapshot = self._attempt_snapshot(attempt_id)
            skip_reason = self._skip_reason(snapshot)
            if skip_reason is not None:
                return AutoIterationAttemptResult(
                    attempt_id=attempt_id,
                    status="skipped" if skip_reason != "attempt_succeeded" else "succeeded",
                    iteration_count=int(state.get("iteration_count") or 0),
                    reason=skip_reason,
                )
            failure = self._latest_failure(attempt_id)
            if not failure:
                return self._block(attempt_id, state, snapshot, "missing_validation_failure")
            policy = policy_for_validation_failure(failure, operator_triggered=False)
            route, reason = self._select_route(policy, failure, state)
            if route == "blocked":
                return self._block(attempt_id, state, snapshot, reason)
            if route == "exhausted":
                return self._exhaust(attempt_id, state, snapshot, reason)

            before_result = _failure_result(failure)
            before_file = self._challenge_fingerprint(snapshot, failure)
            action_result: str
            next_attempt_id: UUID | None = None
            if route == "deterministic":
                try:
                    action_result = self._deterministic_round(snapshot, failure, policy)
                except BuildAttemptRepairError as exc:
                    return self._block(
                        attempt_id,
                        state,
                        snapshot,
                        f"deterministic_repair_invocation_failed: {exc}",
                    )
                revalidated = self._revalidate(attempt_id)
                if revalidated is None:
                    state = self._after_action(
                        attempt_id,
                        state,
                        snapshot,
                        route=route,
                        policy=policy,
                        failure=failure,
                        action_result=action_result,
                        before_result=before_result,
                        before_file=before_file,
                    )
                    return AutoIterationAttemptResult(
                        attempt_id=attempt_id,
                        status="succeeded",
                        iteration_count=int(state.get("iteration_count") or 0),
                        selected_route=route,
                        reason="revalidation_passed",
                    )
                action_result = f"{action_result}; revalidation_failed: {revalidated}"
            elif route == "hermes":
                action_result = self._hermes_round(attempt_id)
                snapshot_after_hermes = self._attempt_snapshot(attempt_id)
                if snapshot_after_hermes is not None and snapshot_after_hermes.status == "succeeded":
                    state = self._after_action(
                        attempt_id,
                        state,
                        snapshot_after_hermes,
                        route=route,
                        policy=policy,
                        failure=failure,
                        action_result=action_result,
                        before_result=before_result,
                        before_file=before_file,
                    )
                    return AutoIterationAttemptResult(
                        attempt_id=attempt_id,
                        status="succeeded",
                        iteration_count=int(state.get("iteration_count") or 0),
                        selected_route=route,
                        reason="repair_passed",
                )
            elif route == "retry":
                try:
                    next_attempt_id = self._retry_round(attempt_id, policy)
                except BuildOrchestrationError as exc:
                    return self._block(
                        attempt_id,
                        state,
                        snapshot,
                        f"retry_invocation_failed: {exc}",
                    )
                state["retry_count"] = int(state.get("retry_count") or 0) + 1
                self._record_state_event(
                    attempt_id,
                    state,
                    event_type="auto_iteration_action",
                    selected_route="retry",
                    selected_reason=reason,
                    failure_signature=_failure_signature(failure),
                    action_result=f"queued retry attempt {next_attempt_id}",
                    next_status="retry_queued",
                )
                state["status"] = "retry_queued"
                write_json(self._state_path(attempt_id), state)
                self._record_progress(
                    snapshot.shard_basename,
                    "failed",
                    {
                        "event": "auto_iteration_retry_queued",
                        "next_attempt_id": str(next_attempt_id),
                        "iteration_count": state.get("iteration_count", 0),
                    },
                )
                return AutoIterationAttemptResult(
                    attempt_id=attempt_id,
                    status="retry_queued",
                    iteration_count=int(state.get("iteration_count") or 0),
                    selected_route=route,
                    reason=reason,
                    next_attempt_id=next_attempt_id,
                )
            else:
                return self._block(attempt_id, state, snapshot, f"unknown_route:{route}")

            state = self._after_action(
                attempt_id,
                state,
                snapshot,
                route=route,
                policy=policy,
                failure=failure,
                action_result=action_result,
                before_result=before_result,
                before_file=before_file,
            )
            if state.get("status") == "blocked":
                return AutoIterationAttemptResult(
                    attempt_id=attempt_id,
                    status="blocked",
                    iteration_count=int(state.get("iteration_count") or 0),
                    selected_route=route,
                    reason=str(state.get("blocked_reason") or "blocked"),
                )

        snapshot = self._attempt_snapshot(attempt_id)
        if snapshot is None:
            return AutoIterationAttemptResult(
                attempt_id=attempt_id,
                status="skipped",
                iteration_count=int(state.get("iteration_count") or 0),
                reason="attempt_missing",
            )
        return self._exhaust(attempt_id, state, snapshot, "auto_iteration_budget_exhausted")

    def _after_action(
        self,
        attempt_id: UUID,
        state: dict[str, Any],
        snapshot: _AttemptSnapshot,
        *,
        route: str,
        policy: ValidationRepairPolicy,
        failure: Mapping[str, Any],
        action_result: str,
        before_result: Mapping[str, Any],
        before_file: str,
    ) -> dict[str, Any]:
        state["iteration_count"] = int(state.get("iteration_count") or 0) + 1
        if route == "deterministic":
            state["deterministic_count"] = int(state.get("deterministic_count") or 0) + 1
        if route == "hermes":
            state["hermes_count"] = int(state.get("hermes_count") or 0) + 1

        next_snapshot = self._attempt_snapshot(attempt_id)
        next_status = next_snapshot.status if next_snapshot is not None else "missing"
        after_failure = self._latest_failure(attempt_id)
        after_result = _failure_result(after_failure or {})
        after_file = self._challenge_fingerprint(next_snapshot or snapshot, after_failure or failure)
        repeated = (
            validation_failure_fingerprints([before_result])
            == validation_failure_fingerprints([after_result])
        )
        no_progress = repeated and no_progress_repair_blocked(
            before_file_fingerprint=[before_file],
            after_file_fingerprint=[after_file],
            before_results=[before_result],
            after_results=[after_result],
        )
        state["same_no_progress_count"] = (
            int(state.get("same_no_progress_count") or 0) + 1 if no_progress else 0
        )
        if route == "hermes" and no_progress:
            state["status"] = "blocked"
            state["blocked_reason"] = "no_progress_after_hermes_repair"
        elif int(state.get("same_no_progress_count") or 0) >= 2:
            state["status"] = "blocked"
            state["blocked_reason"] = "repeated_validation_failure_without_progress"
        else:
            state["status"] = next_status
            state.pop("blocked_reason", None)

        self._record_state_event(
            attempt_id,
            state,
            event_type="auto_iteration_action",
            selected_route=route,
            selected_reason=policy.summary,
            failure_signature=_failure_signature(failure),
            action_result=action_result,
            next_status=state["status"],
            blocked_reason=state.get("blocked_reason"),
            failure_fingerprint=tuple(validation_failure_fingerprints([after_result])),
            progress_fingerprint=tuple(validation_repair_progress_fingerprints([after_result])),
        )
        write_json(self._state_path(attempt_id), state)
        self._record_progress(
            snapshot.shard_basename,
            "failed" if state["status"] == "blocked" else "running",
            {
                "event": "auto_iteration_action",
                "selected_route": route,
                "action_result": action_result[:500],
                "next_status": state["status"],
                "iteration_count": state["iteration_count"],
                "blocked_reason": state.get("blocked_reason"),
            },
        )
        return state

    def _select_route(
        self,
        policy: ValidationRepairPolicy,
        failure: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> tuple[str, str]:
        iteration_count = int(state.get("iteration_count") or 0)
        deterministic_count = int(state.get("deterministic_count") or 0)
        hermes_count = int(state.get("hermes_count") or 0)
        retry_count = int(state.get("retry_count") or 0)
        if iteration_count >= self.budget.max_iterations:
            return "exhausted", "auto_iteration_budget_exhausted"

        deterministic_limit = min(
            self.budget.max_deterministic_repairs,
            policy.max_deterministic_rounds or self.budget.max_deterministic_repairs,
        )
        if (
            policy.route_type == "deterministic"
            and policy.deterministic_mechanics
            and deterministic_count < deterministic_limit
        ):
            return "deterministic", policy.summary

        timeout_like = _timeout_or_repair_invocation_failure(failure)
        if (
            (policy.route_type == "hermes" or policy.hermes_allowed)
            and hermes_count < self.budget.max_hermes_repairs
            and not (timeout_like and hermes_count >= 1)
        ):
            return "hermes", policy.summary

        if self._retry_allowed(failure, policy) and retry_count < self.budget.max_retry_attempts:
            return "retry", "bounded retry for infrastructure or exhausted repair route"

        if retry_count >= self.budget.max_retry_attempts and self._retry_allowed(failure, policy):
            return "exhausted", "retry_attempt_budget_exhausted"
        if hermes_count >= self.budget.max_hermes_repairs and (policy.route_type == "hermes" or policy.hermes_allowed):
            return "exhausted", "hermes_repair_budget_exhausted"
        if deterministic_count >= deterministic_limit and policy.route_type == "deterministic":
            return "exhausted", "deterministic_repair_budget_exhausted"
        return "blocked", policy.summary or "no automatic route"

    @staticmethod
    def _retry_allowed(
        failure: Mapping[str, Any],
        policy: ValidationRepairPolicy,
    ) -> bool:
        failure_class = str(failure.get("validation_failure_class") or policy.failure_class or "")
        status = str(failure.get("validation_status") or "")
        repair_result = str(failure.get("repair_result") or "")
        return (
            failure_class in {"timeout", "validation_inconclusive"}
            or status in {"validator_error", "missing_challenge"}
            or repair_result == "repair_invocation_failed"
        )

    def _deterministic_round(
        self,
        snapshot: _AttemptSnapshot,
        failure: Mapping[str, Any],
        policy: ValidationRepairPolicy,
    ) -> str:
        challenge_dir = self._challenge_dir(snapshot, failure)
        result = self.deterministic_repair(
            challenge_dir,
            challenge_id=str(failure.get("challenge_id") or snapshot.challenge_id or ""),
            allowed_mechanics=policy.deterministic_mechanics,
        )
        if result.changed:
            return "deterministic_changed: " + "; ".join(result.actions)
        return "deterministic_no_change"

    def _hermes_round(self, attempt_id: UUID) -> str:
        try:
            result = self.repair_service.repair(attempt_id)
        except BuildAttemptRepairError as exc:
            return f"repair_invocation_failed: {exc}"
        if result.status == "succeeded":
            return f"hermes_repair_succeeded: {result.repair_id}"
        return f"hermes_repair_failed: {result.failure_summary or result.status}"

    def _retry_round(self, attempt_id: UUID, policy: ValidationRepairPolicy) -> UUID:
        try:
            if policy.hermes_allowed or policy.route_type == "hermes":
                return self.orchestration_service.repair(attempt_id)
            return self.orchestration_service.retry(attempt_id)
        except BuildOrchestrationError:
            raise

    def _revalidate(self, attempt_id: UUID) -> str | None:
        try:
            self.revalidation_service.revalidate(attempt_id)
        except BuildAttemptRevalidationError as exc:
            return str(exc)
        return None

    def _block(
        self,
        attempt_id: UUID,
        state: dict[str, Any],
        snapshot: _AttemptSnapshot,
        reason: str,
    ) -> AutoIterationAttemptResult:
        state["status"] = "blocked"
        state["blocked_reason"] = reason
        self._record_state_event(
            attempt_id,
            state,
            event_type="auto_iteration_blocked",
            selected_route="blocked",
            action_result="blocked",
            next_status=snapshot.status,
            blocked_reason=reason,
            iteration_count=state.get("iteration_count", 0),
        )
        write_json(self._state_path(attempt_id), state)
        self._record_progress(
            snapshot.shard_basename,
            "failed",
            {
                "event": "auto_iteration_blocked",
                "blocked_reason": reason,
                "iteration_count": state.get("iteration_count", 0),
            },
        )
        return AutoIterationAttemptResult(
            attempt_id=attempt_id,
            status="blocked",
            iteration_count=int(state.get("iteration_count") or 0),
            selected_route="blocked",
            reason=reason,
        )

    def _exhaust(
        self,
        attempt_id: UUID,
        state: dict[str, Any],
        snapshot: _AttemptSnapshot,
        reason: str,
    ) -> AutoIterationAttemptResult:
        state["status"] = "exhausted"
        state["blocked_reason"] = reason
        self._record_state_event(
            attempt_id,
            state,
            event_type="auto_iteration_exhausted",
            selected_route="blocked",
            action_result="exhausted",
            next_status=snapshot.status,
            blocked_reason=reason,
            iteration_count=state.get("iteration_count", 0),
        )
        write_json(self._state_path(attempt_id), state)
        self._record_progress(
            snapshot.shard_basename,
            "failed",
            {
                "event": "auto_iteration_exhausted",
                "blocked_reason": reason,
                "iteration_count": state.get("iteration_count", 0),
            },
        )
        return AutoIterationAttemptResult(
            attempt_id=attempt_id,
            status="exhausted",
            iteration_count=int(state.get("iteration_count") or 0),
            selected_route="blocked",
            reason=reason,
        )

    def _latest_failed_attempt_ids(self, limit: int) -> list[UUID]:
        with transaction(factory=self.session_factory) as session:
            rows = BuildAttemptsRepository(session).list_attempts(
                status="failed",
                limit=limit,
            )
            return [row.id for row in rows]

    def _attempt_snapshot(self, attempt_id: UUID) -> _AttemptSnapshot | None:
        with transaction(factory=self.session_factory) as session:
            row = session.get(build_model.BuildAttempt, attempt_id)
            if row is None:
                return None
            latest = BuildAttemptsRepository(session).latest_for_design_task(row.design_task_id)
            active = BuildAttemptsRepository(session).active_for_design_task(row.design_task_id)
            task = session.get(task_model.DesignTask, row.design_task_id)
            return _AttemptSnapshot(
                id=row.id,
                design_task_id=row.design_task_id,
                status=row.status,
                shard_basename=row.shard_basename,
                resulting_challenge_dir=row.resulting_challenge_dir,
                category=task.category if task is not None else None,
                challenge_id=task.challenge_id if task is not None else None,
                current_execution_id=row.current_execution_id,
                latest_execution_id=row.latest_execution_id,
                is_latest=latest is not None and latest.id == row.id,
                has_active_attempt=active is not None and active.id != row.id,
                task_status=task.status if task is not None else None,
            )

    @staticmethod
    def _skip_reason(snapshot: _AttemptSnapshot | None) -> str | None:
        if snapshot is None:
            return "attempt_missing"
        if snapshot.status == "succeeded":
            return "attempt_succeeded"
        if snapshot.status != "failed":
            return f"attempt_status_{snapshot.status}"
        if not snapshot.is_latest:
            return "not_latest_attempt"
        if snapshot.current_execution_id is not None:
            return "attempt_has_current_execution"
        if snapshot.has_active_attempt:
            return "design_task_has_active_attempt"
        if snapshot.task_status != "build_failed":
            return f"task_status_{snapshot.task_status or 'missing'}"
        return None

    def _latest_failure(self, attempt_id: UUID) -> dict[str, Any] | None:
        failure = latest_failed_validation(self.paths, attempt_id)
        return dict(failure) if isinstance(failure, Mapping) and not failure.get("failed_count") else None

    def _challenge_dir(self, snapshot: _AttemptSnapshot, failure: Mapping[str, Any]) -> Path:
        payload = _attempt_payload(self.paths, snapshot.shard_basename)
        challenge_ids = _challenge_ids(payload)
        challenge_id = str(failure.get("challenge_id") or snapshot.challenge_id or "")
        if not challenge_id and len(challenge_ids) == 1:
            challenge_id = challenge_ids[0]
        if not challenge_id:
            raise BuildAttemptRepairError("auto iteration requires a challenge id")
        return _challenge_directory(
            self.paths,
            snapshot.id,
            challenge_id,
            snapshot.resulting_challenge_dir,
            category=snapshot.category,
        )

    def _challenge_fingerprint(
        self,
        snapshot: _AttemptSnapshot,
        failure: Mapping[str, Any],
    ) -> str:
        try:
            return _challenge_file_fingerprint(self._challenge_dir(snapshot, failure))
        except (BuildAttemptRepairError, OSError):
            return ""

    @contextmanager
    def _attempt_advisory_lock(self, attempt_id: UUID) -> Iterator[bool]:
        key = attempt_id.int & ((1 << 63) - 1)
        with self.session_factory.engine.connect() as connection:
            acquired = bool(connection.scalar(sa.select(sa.func.pg_try_advisory_lock(key))))
            connection.commit()
            if not acquired:
                yield False
                return
            try:
                yield True
            finally:
                connection.execute(sa.select(sa.func.pg_advisory_unlock(key)))
                connection.commit()

    def _record_progress(self, shard_basename: str, status: str, payload: Mapping[str, Any]) -> None:
        try:
            self.progress.record(
                shard=shard_basename,
                stage="validate",
                status=status,
                worker=AUTO_ITERATION_WORKER,
                message="auto_iteration " + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        except Exception:  # noqa: BLE001 - progress must not hide the real route result
            LOG.exception("failed to record auto-iteration progress event")

    def _load_state(self, attempt_id: UUID) -> dict[str, Any]:
        state = read_json(self._state_path(attempt_id), {})
        if not isinstance(state, dict):
            state = {}
        state.setdefault("attempt_id", str(attempt_id))
        state.setdefault("status", "running")
        state.setdefault("iteration_count", 0)
        state.setdefault("deterministic_count", 0)
        state.setdefault("hermes_count", 0)
        state.setdefault("retry_count", 0)
        state.setdefault("same_no_progress_count", 0)
        state.setdefault("events", [])
        return state

    def _state_counts(self, attempt_id: UUID) -> dict[str, int]:
        state = self._load_state(attempt_id)
        return {
            "iteration_count": int(state.get("iteration_count") or 0),
            "deterministic_count": int(state.get("deterministic_count") or 0),
            "hermes_count": int(state.get("hermes_count") or 0),
            "retry_count": int(state.get("retry_count") or 0),
        }

    def _record_state_event(
        self,
        attempt_id: UUID,
        state: dict[str, Any],
        *,
        event_type: str,
        selected_route: str,
        action_result: str | None = None,
        next_status: str | None = None,
        selected_reason: str | None = None,
        failure_signature: str | None = None,
        blocked_reason: str | None = None,
        iteration_count: Any | None = None,
        **extra: Any,
    ) -> None:
        event = {
            "created_at": beijing_now_isoformat(),
            "event": event_type,
            "selected_route": selected_route,
            "selected_reason": selected_reason,
            "failure_signature": failure_signature,
            "action_result": action_result,
            "next_status": next_status,
            "blocked_reason": blocked_reason,
            "iteration_count": int(
                iteration_count
                if iteration_count is not None
                else state.get("iteration_count") or 0
            ),
            **{key: value for key, value in extra.items() if value not in (None, "", [])},
        }
        events = state.setdefault("events", [])
        if isinstance(events, list):
            events.append({key: value for key, value in event.items() if value not in (None, "", [])})
            del events[:-100]
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(self._state_path(attempt_id), state)

    def _state_path(self, attempt_id: UUID) -> Path:
        return self.paths.executions / str(attempt_id) / "auto-iteration.json"


def read_auto_iteration_state(paths: ProjectPaths, attempt_id: UUID) -> dict[str, Any]:
    state = read_json(paths.executions / str(attempt_id) / "auto-iteration.json", {})
    return state if isinstance(state, dict) else {}


def _failure_result(failure: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(failure)
    result.setdefault("solve_status", "failed")
    if "challenge_id" not in result and result.get("id"):
        result["challenge_id"] = result["id"]
    return result


def _failure_signature(failure: Mapping[str, Any]) -> str | None:
    value = failure.get("validation_failure_signature")
    return str(value) if value not in (None, "") else None


def _timeout_or_repair_invocation_failure(failure: Mapping[str, Any]) -> bool:
    return (
        failure.get("validation_failure_class") == "timeout"
        or failure.get("validation_status") == "timeout"
        or failure.get("repair_result") == "repair_invocation_failed"
    )
