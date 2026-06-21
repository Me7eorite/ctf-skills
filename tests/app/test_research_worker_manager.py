"""Research worker startup-handshake contracts."""

from __future__ import annotations

from core.paths import ProjectPaths
from web import research_worker_manager as manager_module
from web.research_worker_manager import ResearchWorkerManager


class _FakeProcess:
    pid = 4242

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout):
        return 0

    def kill(self):
        self.killed = True


def _manager(tmp_path, monkeypatch, *, handshake: bool):
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    process = _FakeProcess()
    monkeypatch.setattr(manager_module.subprocess, "Popen", lambda *_a, **_kw: process)
    manager = ResearchWorkerManager(paths)
    monkeypatch.setattr(manager, "_wait_for_handshake", lambda *_a, **_kw: handshake)
    return manager, process


def test_handshake_success_returns_started(tmp_path, monkeypatch):
    manager, process = _manager(tmp_path, monkeypatch, handshake=True)

    ok, message = manager.start(kind="once", max_jobs=1)

    assert ok is True
    assert "research worker started" in message
    assert process.terminated is False


def test_handshake_timeout_terminates_and_reports_stderr_tail(tmp_path, monkeypatch):
    manager, process = _manager(tmp_path, monkeypatch, handshake=False)
    monkeypatch.setattr(manager, "_log_tail", lambda: "import failed: boom")

    ok, message = manager.start(kind="once", max_jobs=1)

    assert ok is False
    assert message == "worker_startup_failed: import failed: boom"
    assert process.terminated is True


def test_dead_handshake_files_are_swept_on_start(tmp_path, monkeypatch):
    manager, _process = _manager(tmp_path, monkeypatch, handshake=True)
    dead = manager.paths.worker_handshake / "999999.ready"
    live = manager.paths.worker_handshake / "123.ready"
    malformed = manager.paths.worker_handshake / "invalid.ready"
    for marker in (dead, live, malformed):
        marker.touch()
    monkeypatch.setattr(manager_module, "_pid_exists", lambda pid: pid == 123)

    manager._sweep_dead_handshake_files()

    assert not dead.exists()
    assert live.exists()
    assert not malformed.exists()
