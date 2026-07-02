"""Execution wrapper for one structured challenge-design Hermes attempt."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from core.paths import ProjectPaths
from hermes.design import invoke_design_agent
from hermes.process import HERMES_TIMEOUT_RETURNCODE, HermesProcessResult

DesignInvoke = Callable[..., HermesProcessResult]
DesignExecutionResult = tuple[str, int, float]


class DesignChallengeExecutor:
    """Run Hermes once for a rendered design prompt.

    The executor owns no database state and does not parse Hermes stdout. The
    service layer maps the returned exit code into attempt persistence.
    """

    def __init__(
        self,
        paths: ProjectPaths,
        hermes_invoke: DesignInvoke = invoke_design_agent,
    ) -> None:
        self.paths = paths
        self.hermes_invoke = hermes_invoke

    def execute(
        self,
        prompt_text: str,
        profile_name: str,
        timeout_seconds: int,
        log_path: str | Path,
        workspace: str | Path,
    ) -> DesignExecutionResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        workspace_path = Path(workspace)
        workspace_path.mkdir(parents=True, exist_ok=True)
        started_at = time.monotonic()
        result = self.hermes_invoke(
            prompt_text,
            profile_name=profile_name,
            log_path=Path(log_path),
            timeout=timeout_seconds,
            paths=self.paths,
            cwd=workspace_path,
        )
        duration_s = time.monotonic() - started_at
        return result.stdout, result.returncode, duration_s


def last_error_for_exit_code(exit_code: int) -> str | None:
    """Translate Hermes exit code into the persisted attempt error string."""
    if exit_code == 0:
        return None
    if exit_code == HERMES_TIMEOUT_RETURNCODE:
        return "timeout"
    return f"Hermes exited with {exit_code}"
