"""Unit tests for heartbeat fault tolerance in ResearchAgentExecutor."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.research_agent_executor import ResearchAgentExecutor


class _FakeJobService:
    """Replays a scripted sequence of heartbeat outcomes."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.call_count = 0

    def heartbeat(self, _run_id, _agent_id, _claim_token, _lease_seconds):
        self.call_count += 1
        if not self._outcomes:
            return True
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _stub_executor(job_service):
    executor = ResearchAgentExecutor.__new__(ResearchAgentExecutor)
    executor.job_service = job_service
    return executor


def _run(target, *args, timeout=2.0):
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "heartbeat thread did not exit"


def test_heartbeat_tolerates_transient_failures(monkeypatch):
    monkeypatch.setattr(
        "services.research_agent_executor.HEARTBEAT_INTERVAL_SECONDS",
        0.005,
    )
    # 一次 DB 异常 + 一次 False + 之后稳定成功，不应触发 lost_lease。
    job = _FakeJobService(
        [False, RuntimeError("transient db error"), True, True, True, True],
    )
    executor = _stub_executor(job)
    run = SimpleNamespace(id=uuid4(), claim_token=uuid4())
    stop_event, lost_lease = threading.Event(), threading.Event()

    thread = threading.Thread(
        target=executor._heartbeat_loop,
        args=(run, "agent-1", 600, stop_event, lost_lease),
        daemon=True,
    )
    thread.start()
    time.sleep(0.06)
    stop_event.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert not lost_lease.is_set()
    assert job.call_count >= 4


def test_heartbeat_gives_up_after_sustained_failures(monkeypatch):
    # lease=0.03 / interval=0.005 → max_failures = max(1, int(2.0)) = 2，便于在测试里观察放弃路径。
    monkeypatch.setattr(
        "services.research_agent_executor.HEARTBEAT_INTERVAL_SECONDS",
        0.005,
    )
    job = _FakeJobService([False] * 30)
    executor = _stub_executor(job)
    run = SimpleNamespace(id=uuid4(), claim_token=uuid4())
    stop_event, lost_lease = threading.Event(), threading.Event()

    _run(
        executor._heartbeat_loop,
        run,
        "agent-1",
        0.03,
        stop_event,
        lost_lease,
    )

    assert lost_lease.is_set()
    assert job.call_count >= 2
