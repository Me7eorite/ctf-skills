"""Hermes invocation wrapper for structured challenge design."""

from __future__ import annotations

import os
from pathlib import Path

from core.paths import ProjectPaths
from hermes import process as hermes_process
from hermes.process import HermesProcessResult, invoke_capture


def invoke_design_agent(
    prompt: str,
    *,
    profile_name: str,
    log_path: Path,
    timeout: int,
    paths: ProjectPaths,
) -> HermesProcessResult:
    """Run Hermes for one design prompt and capture stdout plus a log file."""
    hermes_arguments = _build_arguments(profile_name)
    environment_map = os.environ.copy()
    if paths.hermes_home.exists() and not environment_map.get("HERMES_HOME"):
        environment_map["HERMES_HOME"] = str(paths.hermes_home)
    if hermes_process.apply_legacy_custom_provider(paths.hermes_home, environment_map):
        hermes_process.remove_conflicting_custom_pool(paths.hermes_home)
        query_flag_index = (
            hermes_arguments.index("-q") if "-q" in hermes_arguments else len(hermes_arguments)
        )
        hermes_arguments[query_flag_index:query_flag_index] = ["--provider", "custom"]

    return invoke_capture(
        prompt,
        arguments=hermes_arguments,
        log_path=log_path,
        cwd=paths.root,
        environment=environment_map,
        timeout=timeout,
    )


def _build_arguments(profile_name: str) -> list[str]:
    """Inject `-p <profile_name>` before the Hermes `chat` subcommand."""
    base_arguments = hermes_process.hermes_arguments()
    try:
        chat_index = base_arguments.index("chat")
    except ValueError:
        chat_index = 1 if base_arguments else 0
    return [*base_arguments[:chat_index], "-p", profile_name, *base_arguments[chat_index:]]
