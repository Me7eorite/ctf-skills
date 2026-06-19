"""Subprocess-based controller for the research worker.

Operators trigger a `cli research worker ...` run from the dashboard
without touching a terminal. Mirrors the pattern in
`web.dashboard.TaskManager` but keeps a dedicated slot so the shard
worker and the research worker can run side by side without fighting
over the same `_process` field.

Singleton — one process at a time per running dashboard. Output is
captured to `work/research/logs/web-worker.log` so failures can be
diagnosed without restarting the dashboard.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core.paths import ProjectPaths

_DEFAULT_AGENT_ID = "dashboard"
_DEFAULT_LEASE_SECONDS = 900
_DEFAULT_HERMES_TIMEOUT_SECONDS = 810
_DEFAULT_POLL_INTERVAL_SECONDS = 5.0
_LOG_FILE_NAME = "web-worker.log"


class ResearchWorkerManager:
    """Owns at most one `cli research worker` subprocess at a time."""

    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._kind: str | None = None  # "once" | "loop"
        self._agent_id: str | None = None
        self._started_at: str | None = None
        self._log_path: Path | None = None
        self._exit_message: str | None = None
        self._max_jobs: int | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        kind: str,
        agent_id: str | None = None,
        max_jobs: int = 1,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        hermes_timeout_seconds: int = _DEFAULT_HERMES_TIMEOUT_SECONDS,
        generation_request_id: str | None = None,
    ) -> tuple[bool, str]:
        """Spawn a research worker subprocess.

        Returns (ok, message). ok=False means another worker is already
        running or the requested configuration is invalid.
        """
        if kind not in {"once", "loop"}:
            return False, f"unknown worker kind {kind!r}"
        if hermes_timeout_seconds >= lease_seconds:
            return False, (
                f"hermes_timeout_seconds ({hermes_timeout_seconds}) must be "
                f"less than lease_seconds ({lease_seconds})"
            )
        if kind == "once" and max_jobs <= 0:
            return False, "max_jobs must be a positive integer when running once"

        with self._lock:
            if self._process and self._process.poll() is None:
                return False, "research worker is already running"

            agent = agent_id or _DEFAULT_AGENT_ID
            cli_script = Path(__file__).resolve().parents[1] / "cli.py"
            argv = [
                sys.executable,
                str(cli_script),
                "research",
                "worker",
                "--agent-id",
                agent,
                "--lease-seconds",
                str(lease_seconds),
                "--hermes-timeout-seconds",
                str(hermes_timeout_seconds),
                "--poll-interval-seconds",
                str(_DEFAULT_POLL_INTERVAL_SECONDS),
            ]
            if kind == "loop":
                argv.append("--loop")
            else:
                argv.extend(["--max-jobs", str(max_jobs)])
            if generation_request_id:
                argv.extend(["--generation-request-id", generation_request_id])

            self.paths.research_logs.mkdir(parents=True, exist_ok=True)
            log_path = self.paths.research_logs / _LOG_FILE_NAME
            log_handle = log_path.open("w", encoding="utf-8")
            try:
                # Inherit DATABASE_URL et al.; the CLI handles dotenv itself.
                process = subprocess.Popen(
                    argv,
                    cwd=self.paths.root,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                )
            except FileNotFoundError as exc:
                log_handle.close()
                return False, f"failed to spawn worker: {exc}"

            self._process = process
            self._kind = kind
            self._agent_id = agent
            self._started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._log_path = log_path
            self._exit_message = None
            self._max_jobs = max_jobs if kind == "once" else None

        # Give the subprocess a moment to either crash immediately or settle.
        time.sleep(0.2)
        rc = process.poll()
        if rc is not None and rc != 0:
            detail = self._log_tail()
            return False, f"worker exited immediately rc={rc}: {detail}"
        scope = f", request={generation_request_id}" if generation_request_id else ""
        return True, f"research worker started (kind={kind}, agent={agent}{scope})"

    def stop(self) -> tuple[bool, str]:
        """Terminate the running worker. SIGTERM → wait 5s → SIGKILL."""
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return False, "research worker is not running"
            try:
                process.terminate()
            except ProcessLookupError:
                return True, "research worker was already gone"
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return True, "research worker stopped"

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """Snapshot suitable for the dashboard status endpoint."""
        with self._lock:
            if self._process is None:
                return {"running": False, "last_log": None}

            rc = self._process.poll()
            running = rc is None
            message = self._current_message(rc)
            log_path = (
                str(self._log_path.relative_to(self.paths.root))
                if self._log_path and self._log_path.exists()
                else None
            )
            return {
                "running": running,
                "kind": self._kind,
                "agent_id": self._agent_id,
                "started_at": self._started_at,
                "returncode": rc,
                "max_jobs": self._max_jobs,
                "message": message,
                "log_path": log_path,
                "log_tail": self._log_tail() if not running else "",
            }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _current_message(self, returncode: int | None) -> str:
        if returncode is None:
            return f"research worker '{self._agent_id}' running"
        if returncode == 0:
            return f"research worker '{self._agent_id}' finished cleanly"
        return f"research worker '{self._agent_id}' exited rc={returncode}"

    def _log_tail(self) -> str:
        if self._log_path is None or not self._log_path.exists():
            return ""
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines[-8:])
