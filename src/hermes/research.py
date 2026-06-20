"""Hermes Research Agent 调用封装。

这是 `hermes.process.invoke_capture` 的轻量封装：负责解析 Hermes 命令、
在 `chat` 前注入 `-p <profile_name>`、应用旧版 custom provider 兼容逻辑，
并透传 `cancel_event`，让上层执行器在租约丢失时终止 Hermes。
"""

from __future__ import annotations

import fnmatch
import os
import threading
from pathlib import Path

from core.paths import ProjectPaths
from hermes import process as hermes_process
from hermes.process import HermesProcessResult, invoke_capture

RESEARCH_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "USER",
    "SHELL",
    "HERMES_HOME",
    "HERMES_TIMEOUT",
    "HERMES_CMD",
}
RESEARCH_ENV_ALLOWLIST_PATTERNS = (
    "ANTHROPIC_*",
    "OPENAI_*",
    "CUSTOM_PROVIDER_*",
    "CUSTOM_*",
)
RESEARCH_ENV_DENY_KEYWORDS = (
    "DATABASE",
    "POSTGRES",
    "PASSWORD",
    "TOKEN",
    "SECRET",
    "PRIVATE_KEY",
)


def invoke_research_agent(
    prompt: str,
    *,
    profile_name: str,
    log_path: Path,
    timeout: int,
    paths: ProjectPaths,
    cancel_event: threading.Event | None = None,
) -> HermesProcessResult:
    """使用指定 profile 运行 Hermes Research Agent，并捕获 stdout。"""
    # 中文注释：组装 Hermes Research Agent 的命令和环境，再委托通用捕获执行器运行。
    hermes_arguments = _build_arguments(profile_name)
    environment_map = _build_research_env(paths)
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
        cancel_event=cancel_event,
    )


def _build_research_env(paths: ProjectPaths) -> dict[str, str]:
    """Build the minimal environment inherited by the Hermes research process."""
    environment_map: dict[str, str] = {}
    for key, value in os.environ.items():
        upper_key = key.upper()
        if any(keyword in upper_key for keyword in RESEARCH_ENV_DENY_KEYWORDS):
            continue
        if key in RESEARCH_ENV_ALLOWLIST or any(
            fnmatch.fnmatchcase(key, pattern) for pattern in RESEARCH_ENV_ALLOWLIST_PATTERNS
        ):
            environment_map[key] = value
    if paths.hermes_home.exists() and not environment_map.get("HERMES_HOME"):
        environment_map["HERMES_HOME"] = str(paths.hermes_home)
    return environment_map


def _build_arguments(profile_name: str) -> list[str]:
    """把 `-p <profile_name>` 注入到基础命令里的 `chat` 子命令之前。

    兼容 `hermes chat ...`、`uvx --from hermes-agent hermes chat ...`，
    以及通过 `HERMES_CMD` 覆盖的命令。若命令里没有 `chat`，则退回到
    二进制名之后插入，尽量让 Hermes 尽早看到 profile 参数。
    """
    # 中文注释：在 Hermes 子命令 `chat` 前插入 profile 参数，兼容 uvx 包装命令。
    base_arguments = hermes_process.hermes_arguments()
    try:
        chat_index = base_arguments.index("chat")
    except ValueError:
        chat_index = 1 if base_arguments else 0
    return [*base_arguments[:chat_index], "-p", profile_name, *base_arguments[chat_index:]]
