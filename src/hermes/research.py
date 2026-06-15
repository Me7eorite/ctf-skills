"""Hermes Research Agent invocation.

Thin wrapper over `hermes.process.invoke_capture` that resolves the Hermes
binary, injects `-p <profile_name>` into the argv before `chat`, applies the
legacy custom-provider compatibility shim, and forwards a `cancel_event` so
the upstream executor can terminate Hermes when its claim lease is lost.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from core.paths import ProjectPaths
from hermes import process as hermes_process
from hermes.process import HermesProcessResult, invoke_capture


def invoke_research_agent(
    prompt: str,
    *,
    profile_name: str,
    log_path: Path,
    timeout: int,
    paths: ProjectPaths,
    cancel_event: threading.Event | None = None,
) -> HermesProcessResult:
    """Run the Hermes Research Agent under `profile_name`, capturing stdout."""
    arguments = _build_arguments(profile_name)
    environment = os.environ.copy()
    if paths.hermes_home.exists() and not environment.get("HERMES_HOME"):
        environment["HERMES_HOME"] = str(paths.hermes_home)
    if hermes_process.apply_legacy_custom_provider(paths.hermes_home, environment):
        hermes_process.remove_conflicting_custom_pool(paths.hermes_home)
        query_index = arguments.index("-q") if "-q" in arguments else len(arguments)
        arguments[query_index:query_index] = ["--provider", "custom"]

    return invoke_capture(
        prompt,
        arguments=arguments,
        log_path=log_path,
        cwd=paths.root,
        environment=environment,
        timeout=timeout,
        cancel_event=cancel_event,
    )


def _build_arguments(profile_name: str) -> list[str]:
    """Inject `-p <profile_name>` immediately before `chat` in the base argv.

    Works for `hermes chat ...`, `uvx --from hermes-agent hermes chat ...`,
    and `HERMES_CMD`-overridden variants — all of which contain `chat` as a
    subcommand. If no `chat` token is present, fall back to inserting after
    the binary so Hermes still sees `-p` as an early option.
    """
    base = hermes_process.hermes_arguments()
    try:
        index = base.index("chat")
    except ValueError:
        index = 1 if base else 0
    return [*base[:index], "-p", profile_name, *base[index:]]
