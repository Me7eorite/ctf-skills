from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest

import cli
from cli import _parse_sequential_failfast_streak, _run_build_attempt_sequence

IDS = [
    UUID("00000000-0000-0000-0000-000000000001"),
    UUID("00000000-0000-0000-0000-000000000002"),
    UUID("00000000-0000-0000-0000-000000000003"),
    UUID("00000000-0000-0000-0000-000000000004"),
    UUID("00000000-0000-0000-0000-000000000005"),
    UUID("00000000-0000-0000-0000-000000000006"),
    UUID("00000000-0000-0000-0000-000000000007"),
    UUID("00000000-0000-0000-0000-000000000008"),
    UUID("00000000-0000-0000-0000-000000000009"),
    UUID("00000000-0000-0000-0000-000000000010"),
    UUID("00000000-0000-0000-0000-000000000011"),
    UUID("00000000-0000-0000-0000-000000000012"),
]


class FakeRunner:
    def __init__(self, phases):
        self.phases = list(phases)
        self.calls = []

    def run(self, worker, **kwargs):
        self.calls.append((worker, kwargs))
        phase = self.phases.pop(0)
        if isinstance(phase, BaseException):
            raise phase
        if phase == "success":
            return {
                "processed": 1,
                "failed": 0,
                "outcomes": [
                    {"status": "done", "shard": str(kwargs["build_attempt_id"])}
                ],
            }
        return {
            "processed": 1,
            "failed": 1,
            "outcomes": [
                {
                    "status": "failed",
                    "shard": str(kwargs["build_attempt_id"]),
                    "hermes_phase": phase,
                }
            ],
        }


def _run(phases, *, threshold=2, ids=None):
    return _run_build_attempt_sequence(
        FakeRunner(phases),
        "worker-1",
        ids or IDS[: len(phases)],
        timeout=None,
        timeout_source=None,
        failfast_streak=threshold,
    )


def test_two_consecutive_auth_failures_abort_tail():
    result = _run(["success", "success", "success", "success", "success", "hermes_auth", "hermes_auth"], ids=IDS)

    assert result["processed"] == 7
    assert result["failed"] == 2
    assert result["abort_reason"] == "consecutive_infra"
    assert result["aborted"] == [str(item) for item in IDS[7:]]
    assert [item["status"] for item in result["outcomes"][-5:]] == ["aborted"] * 5


def test_auth_then_rate_limit_reaches_same_streak():
    result = _run(["hermes_auth", "hermes_rate_limit", "success"], ids=IDS[:3])

    assert result["abort_reason"] == "consecutive_infra"
    assert result["processed"] == 2
    assert result["aborted"] == [str(IDS[2])]


def test_runtime_failures_do_not_abort():
    result = _run(["hermes_runtime", "hermes_runtime", "success"], ids=IDS[:3])

    assert result["abort_reason"] is None
    assert result["processed"] == 3
    assert result["failed"] == 2
    assert result["aborted"] == []


def test_validation_failures_continue_and_do_not_increment_infra_streak():
    result = _run(
        ["validation", "validation", "hermes_auth", "validation", "hermes_auth", "success"],
        ids=IDS[:6],
    )

    assert result["abort_reason"] is None
    assert result["processed"] == 6
    assert result["failed"] == 5
    assert result["aborted"] == []
    assert [item["shard"] for item in result["outcomes"]] == [str(item) for item in IDS[:6]]


def test_failfast_threshold_zero_disables_streak():
    result = _run(["hermes_auth", "hermes_auth", "success"], threshold=0, ids=IDS[:3])

    assert result["abort_reason"] is None
    assert result["processed"] == 3
    assert result["aborted"] == []


def test_cancelled_outcome_aborts_immediately():
    result = _run(["hermes_cancelled", "success"], ids=IDS[:2])

    assert result["abort_reason"] == "interrupt"
    assert result["processed"] == 1
    assert result["aborted"] == [str(IDS[1])]
    assert result["interrupted_attempt"] is None


def test_keyboard_interrupt_records_in_flight_and_aborts_tail():
    result = _run(["success", KeyboardInterrupt()], ids=IDS[:4])

    assert result["abort_reason"] == "interrupt"
    assert result["processed"] == 1
    assert result["failed"] == 0
    assert result["interrupted_attempt"] == str(IDS[1])
    assert result["aborted"] == [str(IDS[2]), str(IDS[3])]


def test_worker_exception_records_failed_attempt_and_aborts_tail(monkeypatch):
    finalized = []
    monkeypatch.setattr(
        cli,
        "_finalize_build_attempt",
        lambda attempt_id, worker, item: finalized.append((attempt_id, worker, item)),
    )

    result = _run(["success", RuntimeError("postgres unavailable")], ids=IDS[:4])

    assert result["abort_reason"] == "worker_exception"
    assert result["processed"] == 1
    assert result["failed"] == 1
    assert result["interrupted_attempt"] == str(IDS[1])
    assert result["aborted"] == [str(IDS[2]), str(IDS[3])]
    failed = result["outcomes"][1]
    assert failed["status"] == "failed"
    assert failed["shard"] == str(IDS[1])
    assert failed["hermes_phase"] == "worker_exception"
    assert failed["exception_type"] == "RuntimeError"
    assert "postgres unavailable" in failed["error"]
    assert "Traceback" in failed["traceback"]
    assert "RuntimeError: postgres unavailable" in failed["traceback"]
    assert finalized[-1] == (
        IDS[1],
        "worker-1",
        {
            "processed": 0,
            "failed": 1,
            "outcomes": [failed],
        },
    )


def test_lab_incident_shape_stops_after_second_infra_failure():
    phases = [
        "success",
        "success",
        "success",
        "success",
        "success",
        "hermes_cancelled",
        "hermes_auth",
        "hermes_auth",
        "success",
        "success",
        "success",
        "success",
    ]
    result = _run(phases, ids=IDS)

    assert result["abort_reason"] == "interrupt"
    assert result["processed"] == 6
    assert result["aborted"] == [str(item) for item in IDS[6:]]


def test_lab_auth_cascade_without_cancel_stops_at_attempt_eight():
    phases = [
        "success",
        "success",
        "success",
        "success",
        "success",
        "hermes_runtime",
        "hermes_auth",
        "hermes_auth",
        "success",
        "success",
        "success",
        "success",
    ]
    result = _run(phases, ids=IDS)

    assert result["abort_reason"] == "consecutive_infra"
    assert result["processed"] == 8
    assert result["aborted"] == [str(item) for item in IDS[8:]]


@pytest.mark.parametrize("raw, expected", [(None, 2), ("", 2), ("0", 0), ("3", 3)])
def test_parse_failfast_streak(raw, expected):
    assert _parse_sequential_failfast_streak(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "abc"])
def test_parse_failfast_streak_rejects_invalid(raw):
    with pytest.raises(Exception, match="BUILD_SEQ_INFRA_FAILFAST_STREAK"):
        _parse_sequential_failfast_streak(raw)


def test_expired_attempt_deadline_skips_runner(monkeypatch):
    finalized = []
    runner = FakeRunner(["success"])
    monkeypatch.setattr(cli, "_mark_attempt_running", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_finalize_build_attempt",
        lambda attempt_id, worker, item: finalized.append((attempt_id, worker, item)),
    )

    result = _run_build_attempt_sequence(
        runner,
        "worker-1",
        [IDS[0]],
        timeout=10,
        timeout_source="cli",
        attempt_deadline_epoch=0.0,
        failfast_streak=2,
    )

    assert runner.calls == []
    assert result["failed"] == 1
    assert result["processed"] == 0
    outcome = result["outcomes"][0]
    assert outcome["hermes_phase"] == "global_deadline_exceeded"
    assert outcome["timeout_kind"] == "attempt_deadline"
    assert outcome["validation_status"] == "timeout"
    assert finalized[0][2]["failed"] == 1


def test_execution_heartbeat_stops_at_deadline(monkeypatch):
    terminal_updates = []
    heartbeats = []
    latest = SimpleNamespace(
        id=IDS[0],
        status="running",
        claim_token=IDS[1],
    )

    class FakeRepo:
        def __init__(self, _session):
            pass

        def latest_for_attempt(self, _attempt_id):
            return latest

        def update_to_terminal(self, *args, **kwargs):
            terminal_updates.append((args, kwargs))
            latest.status = "failed"

        def heartbeat(self, *args, **kwargs):
            heartbeats.append((args, kwargs))

    @contextmanager
    def fake_transaction(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(cli, "execution_minting_enabled", lambda: True)
    monkeypatch.setattr(cli, "lease_ttl_seconds", lambda: 3)
    monkeypatch.setattr("persistence.repositories.ExecutionsRepository", FakeRepo)
    monkeypatch.setattr("persistence.session.transaction", fake_transaction)

    with cli._execution_heartbeat(IDS[0], attempt_deadline=0.0):
        pass

    assert terminal_updates
    assert terminal_updates[0][1]["status"] == "failed"
    assert terminal_updates[0][1]["exit_class"] == "attempt_deadline"
    assert heartbeats == []
