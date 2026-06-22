from __future__ import annotations

from uuid import UUID

import pytest

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
