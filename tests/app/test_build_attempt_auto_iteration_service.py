from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from core.paths import ProjectPaths
from core.state import InMemoryProgressStore
from domain.build_attempt_auto_repair import AutoRepairResult
from services.build_attempt_auto_iteration_service import (
    AutoIterationAttemptResult,
    AutoIterationBudget,
    BuildAttemptAutoIterationService,
    _AttemptSnapshot,
)
from services.build_attempt_repair_service import BuildAttemptRepairResult
from services.build_attempt_revalidation_service import BuildAttemptRevalidationError


class _FakeRevalidation:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = outcomes or [None]
        self.calls: list[UUID] = []

    def revalidate(self, attempt_id: UUID) -> None:
        self.calls.append(attempt_id)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome


class _FakeRepair:
    def __init__(self, *, status: str = "failed") -> None:
        self.status = status
        self.calls: list[UUID] = []

    def repair(self, attempt_id: UUID) -> BuildAttemptRepairResult:
        self.calls.append(attempt_id)
        return BuildAttemptRepairResult(
            attempt_id=attempt_id,
            repair_id="repair-test",
            status=self.status,
            verification_status="passed" if self.status == "succeeded" else "failed",
            log_path="/tmp/hermes.log",
            events_path="/tmp/repair-events.jsonl",
            failure_summary=None if self.status == "succeeded" else "no change",
        )


class _FakeOrchestration:
    def __init__(self) -> None:
        self.retry_calls: list[UUID] = []
        self.repair_calls: list[UUID] = []
        self.next_id = uuid4()

    def retry(self, attempt_id: UUID) -> UUID:
        self.retry_calls.append(attempt_id)
        return self.next_id

    def repair(self, attempt_id: UUID) -> UUID:
        self.repair_calls.append(attempt_id)
        return self.next_id


def _service(
    tmp_path: Path,
    *,
    revalidation: _FakeRevalidation | None = None,
    repair: _FakeRepair | None = None,
    orchestration: _FakeOrchestration | None = None,
    deterministic=None,
    budget: AutoIterationBudget | None = None,
) -> BuildAttemptAutoIterationService:
    return BuildAttemptAutoIterationService(
        paths=ProjectPaths(root=tmp_path, repository=tmp_path),
        progress=InMemoryProgressStore(),
        session_factory=object(),  # type: ignore[arg-type]
        budget=budget,
        deterministic_repair=deterministic
        or (lambda *args, **kwargs: AutoRepairResult(changed=True, actions=("fixed",))),
        revalidation_service=revalidation or _FakeRevalidation(),  # type: ignore[arg-type]
        repair_service=repair or _FakeRepair(),  # type: ignore[arg-type]
        orchestration_service=orchestration or _FakeOrchestration(),  # type: ignore[arg-type]
    )


def _snapshot(
    attempt_id: UUID,
    *,
    status: str = "failed",
    is_latest: bool = True,
    current_execution_id: UUID | None = None,
    task_status: str = "build_failed",
) -> _AttemptSnapshot:
    return _AttemptSnapshot(
        id=attempt_id,
        design_task_id=uuid4(),
        status=status,
        shard_basename="web-0001.json",
        resulting_challenge_dir=None,
        category="web",
        challenge_id="web-1",
        current_execution_id=current_execution_id,
        latest_execution_id=None,
        is_latest=is_latest,
        has_active_attempt=False,
        task_status=task_status,
    )


def _contract_failure() -> dict:
    return {
        "source": "validation-history",
        "challenge_id": "web-1",
        "validation_status": "contract_failed",
        "validation_failure_class": "contract",
        "validation_failure_signature": "contract:missing_validation",
        "validation_failure_details": [{"code": "missing_validation", "path": "validate.sh"}],
    }


def _solver_failure() -> dict:
    return {
        "source": "validation-history",
        "challenge_id": "web-1",
        "validation_status": "flag_mismatch",
        "validation_failure_class": "solver",
        "validation_failure_signature": "solver:flag_mismatch",
        "validation_failure_details": [{"code": "flag_mismatch", "phase": "validate"}],
        "validation_stdout_tail": "wrong flag",
    }


def _timeout_failure() -> dict:
    return {
        "source": "validation-history",
        "challenge_id": "web-1",
        "validation_status": "timeout",
        "validation_failure_class": "timeout",
        "validation_failure_signature": "timeout:solver_io",
        "validation_failure_details": [{"code": "timeout", "subreason": "solver_io"}],
    }


@contextmanager
def _lock(acquired: bool = True):
    yield acquired


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    service: BuildAttemptAutoIterationService,
    attempt_id: UUID,
    failures: list[dict],
) -> None:
    monkeypatch.setattr(service, "_attempt_advisory_lock", lambda _attempt_id: _lock(True))
    monkeypatch.setattr(service, "_attempt_snapshot", lambda _attempt_id: _snapshot(attempt_id))
    monkeypatch.setattr(service, "_challenge_dir", lambda *_args, **_kwargs: Path("/tmp/challenge"))
    monkeypatch.setattr(service, "_challenge_fingerprint", lambda *_args, **_kwargs: "files-a")

    calls = {"count": 0}

    def latest_failure(_attempt_id: UUID) -> dict:
        index = min(calls["count"], len(failures) - 1)
        calls["count"] += 1
        return failures[index]

    monkeypatch.setattr(service, "_latest_failure", latest_failure)


def test_failed_attempt_with_deterministic_policy_repairs_and_revalidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = uuid4()
    revalidation = _FakeRevalidation([None])
    repair_calls: list[dict] = []

    def deterministic(*args, **kwargs):
        repair_calls.append(kwargs)
        return AutoRepairResult(changed=True, actions=("generated validate.sh",))

    service = _service(tmp_path, revalidation=revalidation, deterministic=deterministic)
    _patch_common(monkeypatch, service, attempt_id, [_contract_failure()])

    result = service.iterate_attempt(attempt_id)

    assert result.status == "succeeded"
    assert result.selected_route == "deterministic"
    assert revalidation.calls == [attempt_id]
    assert repair_calls
    assert "challenge_yml" in repair_calls[0]["allowed_mechanics"]


def test_deterministic_failure_can_continue_to_hermes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = uuid4()
    revalidation = _FakeRevalidation([BuildAttemptRevalidationError("still failed")])
    repair = _FakeRepair(status="succeeded")
    service = _service(tmp_path, revalidation=revalidation, repair=repair)
    _patch_common(monkeypatch, service, attempt_id, [_contract_failure(), _solver_failure(), _solver_failure()])

    statuses = {"current": "failed"}

    def snapshot(_attempt_id: UUID) -> _AttemptSnapshot:
        return _snapshot(attempt_id, status=statuses["current"])

    original_repair = repair.repair

    def repair_and_succeed(_attempt_id: UUID) -> BuildAttemptRepairResult:
        statuses["current"] = "succeeded"
        return original_repair(_attempt_id)

    monkeypatch.setattr(service, "_attempt_snapshot", snapshot)
    monkeypatch.setattr(repair, "repair", repair_and_succeed)

    result = service.iterate_attempt(attempt_id)

    assert result.status == "succeeded"
    assert result.selected_route == "hermes"
    assert revalidation.calls == [attempt_id]
    assert repair.calls == [attempt_id]


def test_hermes_noop_repeated_fingerprint_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = uuid4()
    repair = _FakeRepair(status="failed")
    service = _service(tmp_path, repair=repair)
    _patch_common(monkeypatch, service, attempt_id, [_solver_failure()])

    result = service.iterate_attempt(attempt_id)

    assert result.status == "blocked"
    assert result.reason == "no_progress_after_hermes_repair"
    assert repair.calls == [attempt_id]


def test_timeout_hermes_is_attempted_only_once_before_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = uuid4()
    repair = _FakeRepair(status="failed")
    service = _service(tmp_path, repair=repair)
    _patch_common(monkeypatch, service, attempt_id, [_timeout_failure()])

    result = service.iterate_attempt(attempt_id)

    assert result.status == "blocked"
    assert repair.calls == [attempt_id]


def test_non_latest_attempt_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = uuid4()
    service = _service(tmp_path)
    monkeypatch.setattr(service, "_attempt_advisory_lock", lambda _attempt_id: _lock(True))
    monkeypatch.setattr(
        service,
        "_attempt_snapshot",
        lambda _attempt_id: _snapshot(attempt_id, is_latest=False),
    )

    result = service.iterate_attempt(attempt_id)

    assert result.status == "skipped"
    assert result.reason == "not_latest_attempt"


def test_running_current_attempt_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = uuid4()
    service = _service(tmp_path)
    monkeypatch.setattr(service, "_attempt_advisory_lock", lambda _attempt_id: _lock(True))
    monkeypatch.setattr(
        service,
        "_attempt_snapshot",
        lambda _attempt_id: _snapshot(attempt_id, current_execution_id=uuid4()),
    )

    result = service.iterate_attempt(attempt_id)

    assert result.status == "skipped"
    assert result.reason == "attempt_has_current_execution"


def test_advisory_lock_busy_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = uuid4()
    service = _service(tmp_path)
    monkeypatch.setattr(service, "_attempt_advisory_lock", lambda _attempt_id: _lock(False))

    result = service.iterate_attempt(attempt_id)

    assert result.status == "busy"
    assert result.reason == "advisory_lock_busy"


def test_once_mode_processes_limit_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = [uuid4(), uuid4(), uuid4()]
    service = _service(tmp_path)
    monkeypatch.setattr(service, "_latest_failed_attempt_ids", lambda limit: ids[:limit])
    monkeypatch.setattr(
        service,
        "iterate_attempt",
        lambda attempt_id: AutoIterationAttemptResult(
            attempt_id=attempt_id,
            status="skipped",
            iteration_count=0,
        ),
    )

    result = service.run_once(limit=2)

    assert result.processed == 2
    assert [item.attempt_id for item in result.outcomes] == ids[:2]
