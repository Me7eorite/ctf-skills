"""Runtime bootstrap for Hermes tool isolation.

Hermes v0.17 collapses ordinary CLI/session task ids to ``default`` when it
creates terminal/file-tool containers. Challenge Factory runs concurrent build
attempts in separate workspaces, so the default container key is too broad.
This module is loaded through ``sitecustomize`` inside the Hermes subprocess and
keeps the compatibility fix in this repository instead of patching Hermes in
``/usr/local/lib``.
"""

from __future__ import annotations

import builtins
import os
import sys
from typing import Any

_PATCHED_ATTR = "_ctf_skills_isolation_patched"
_IMPORT_HOOK_ATTR = "_ctf_skills_import_hook_installed"
_ORIGINAL_IMPORT_ATTR = "_ctf_skills_original_import"


def install() -> None:
    _install_import_hook()
    module = sys.modules.get("tools.terminal_tool")
    if module is not None:
        _patch_terminal_tool(module)


def _install_import_hook() -> None:
    if getattr(builtins, _IMPORT_HOOK_ATTR, False):
        return

    original_import = builtins.__import__

    def _ctf_import(name, globals=None, locals=None, fromlist=(), level=0):
        module = original_import(name, globals, locals, fromlist, level)
        if name == "tools.terminal_tool" or (
            name == "tools" and fromlist and "terminal_tool" in fromlist
        ):
            terminal_tool = sys.modules.get("tools.terminal_tool")
            if terminal_tool is not None:
                _patch_terminal_tool(terminal_tool)
        return module

    setattr(builtins, _ORIGINAL_IMPORT_ATTR, original_import)
    setattr(builtins, _IMPORT_HOOK_ATTR, True)
    builtins.__import__ = _ctf_import


def _patch_terminal_tool(terminal_tool: Any) -> None:
    task_id = os.environ.get("CTF_SKILLS_HERMES_TASK_ID", "").strip()
    if not task_id:
        return

    try:
        terminal_tool.register_task_env_overrides(task_id, _task_overrides())
    except Exception:
        return

    original = getattr(terminal_tool, "_resolve_container_task_id", None)
    if callable(original) and not getattr(original, _PATCHED_ATTR, False):

        def _ctf_resolve_container_task_id(raw_task_id: str | None) -> str:
            raw = str(raw_task_id or "")
            existing = getattr(terminal_tool, "_task_env_overrides", {})
            if raw and raw != "default" and raw != task_id and raw in existing:
                return original(raw_task_id)
            return task_id

        setattr(_ctf_resolve_container_task_id, _PATCHED_ATTR, True)
        terminal_tool._resolve_container_task_id = _ctf_resolve_container_task_id

    _patch_env_config(terminal_tool)


def _patch_env_config(terminal_tool: Any) -> None:
    original = getattr(terminal_tool, "_get_env_config", None)
    if not callable(original) or getattr(original, _PATCHED_ATTR, False):
        return
    host_workspace = os.environ.get("CTF_SKILLS_HOST_WORKSPACE", "").strip()
    container_workspace = (
        os.environ.get("CTF_SKILLS_CONTAINER_WORKSPACE", "").strip()
        or "/workspace/current"
    )
    ctf_env = _ctf_env_snapshot()
    ctf_labels = _ctf_label_snapshot()

    def _ctf_get_env_config() -> dict[str, Any]:
        config = dict(original())
        if host_workspace:
            config["cwd"] = container_workspace
            config["host_cwd"] = None
            config["docker_mount_cwd_to_workspace"] = False
            config["docker_volumes"] = [f"{host_workspace}:{container_workspace}"]
        config["container_persistent"] = True
        config["docker_persist_across_processes"] = False
        config["docker_env"] = _merged_docker_env(config.get("docker_env"), ctf_env)
        config["docker_extra_args"] = _merged_docker_extra_args(
            config.get("docker_extra_args"),
            ctf_labels,
        )
        return config

    setattr(_ctf_get_env_config, _PATCHED_ATTR, True)
    terminal_tool._get_env_config = _ctf_get_env_config


def _task_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {"env_type": os.environ.get("TERMINAL_ENV", "docker")}
    cwd = os.environ.get("TERMINAL_CWD")
    if cwd:
        overrides["cwd"] = cwd
    docker_image = os.environ.get("TERMINAL_DOCKER_IMAGE")
    if docker_image:
        overrides["docker_image"] = docker_image
    return overrides


def _ctf_env_snapshot() -> dict[str, str]:
    values: dict[str, str] = {}
    for key in (
        "CTF_SKILLS_EXECUTION_ID",
        "CTF_SKILLS_HERMES_DOCKER_LABEL",
        "CTF_SKILLS_HERMES_TASK_ID",
        "CTF_SKILLS_HERMES_SESSION_HOME",
        "CTF_SKILLS_HOST_WORKSPACE",
        "CTF_SKILLS_CONTAINER_WORKSPACE",
    ):
        value = os.environ.get(key)
        if value:
            values[key] = value
    return values


def _merged_docker_env(current: Any, ctf_env: dict[str, str]) -> dict[str, str]:
    merged = {
        str(key): str(value)
        for key, value in (current if isinstance(current, dict) else {}).items()
    }
    for key, value in ctf_env.items():
        merged[key] = value
    return merged


def _ctf_label_snapshot() -> list[str]:
    return [
        "ctf-skills-owner=ctf-skills",
        _label_value("ctf-skills-execution", "CTF_SKILLS_EXECUTION_ID"),
        _label_value("ctf-skills-hermes-run", "CTF_SKILLS_HERMES_DOCKER_LABEL"),
    ]


def _merged_docker_extra_args(current: Any, labels: list[str | None]) -> list[str]:
    merged = [str(item) for item in current if isinstance(current, list)]
    for label in labels:
        if not label or label in merged:
            continue
        merged.extend(["--label", label])
    return merged


def _label_value(label: str, env_key: str) -> str | None:
    value = os.environ.get(env_key)
    return f"{label}={value}" if value else None
