"""Runtime bootstrap for Hermes tool isolation.

Hermes v0.17 collapses ordinary CLI/session task ids to ``default`` when it
creates terminal/file-tool containers. Challenge Factory runs concurrent build
attempts in separate workspaces, so the default container key is too broad.
This module is loaded through ``sitecustomize`` inside the Hermes subprocess and
keeps the compatibility fix in this repository instead of patching Hermes in
``/usr/local/lib``.
"""

from __future__ import annotations

import os
from typing import Any

_PATCHED_ATTR = "_ctf_skills_isolation_patched"


def install() -> None:
    task_id = os.environ.get("CTF_SKILLS_HERMES_TASK_ID", "").strip()
    if not task_id:
        return
    try:
        from tools import terminal_tool
    except Exception:
        return

    overrides = _task_overrides()
    try:
        terminal_tool.register_task_env_overrides(task_id, overrides)
    except Exception:
        return

    original = getattr(terminal_tool, "_resolve_container_task_id", None)
    if not callable(original) or getattr(original, _PATCHED_ATTR, False):
        return

    def _ctf_resolve_container_task_id(raw_task_id: str | None) -> str:
        raw = str(raw_task_id or "")
        existing = getattr(terminal_tool, "_task_env_overrides", {})
        if raw and raw != "default" and raw != task_id and raw in existing:
            return original(raw_task_id)
        return task_id

    setattr(_ctf_resolve_container_task_id, _PATCHED_ATTR, True)
    terminal_tool._resolve_container_task_id = _ctf_resolve_container_task_id


def _task_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {"env_type": os.environ.get("TERMINAL_ENV", "docker")}
    cwd = os.environ.get("TERMINAL_CWD")
    if cwd:
        overrides["cwd"] = cwd
    docker_image = os.environ.get("TERMINAL_DOCKER_IMAGE")
    if docker_image:
        overrides["docker_image"] = docker_image
    return overrides

