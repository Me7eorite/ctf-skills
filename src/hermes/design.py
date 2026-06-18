"""结构化题目设计的 Hermes 调用封装。

为设计流程提供一个专门的 Hermes 调用入口。
与 research.py 的模式类似：组装参数 → 注入 profile → 委托 process.invoke_capture。
"""

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
    """使用指定 profile 运行 Hermes Design Agent，并捕获 stdout 和日志。

    与 invoke_research_agent 的区别:
      - 不使用 cancel_event（设计流程暂不支持外部取消）
      - 返回 HermesProcessResult（包含 returncode/stdout/cancelled 标志）
    """
    # 组装带 profile 的 Hermes 命令参数
    hermes_arguments = _build_arguments(profile_name)

    # 准备环境变量：设置 HERMES_HOME（如果尚未设置）
    environment_map = os.environ.copy()
    if paths.hermes_home.exists() and not environment_map.get("HERMES_HOME"):
        environment_map["HERMES_HOME"] = str(paths.hermes_home)

    # 处理旧版 custom provider 兼容逻辑
    if hermes_process.apply_legacy_custom_provider(paths.hermes_home, environment_map):
        # 删除冲突的 custom pool 配置
        hermes_process.remove_conflicting_custom_pool(paths.hermes_home)
        # 在 -q 标志前插入 --provider custom
        query_flag_index = (
            hermes_arguments.index("-q") if "-q" in hermes_arguments else len(hermes_arguments)
        )
        hermes_arguments[query_flag_index:query_flag_index] = ["--provider", "custom"]

    # 委托通用捕获执行器运行
    return invoke_capture(
        prompt,
        arguments=hermes_arguments,
        log_path=log_path,
        cwd=paths.root,
        environment=environment_map,
        timeout=timeout,
    )


def _build_arguments(profile_name: str) -> list[str]:
    """在 Hermes 命令的 `chat` 子命令前注入 `-p <profile_name>`。

    兼容多种命令格式:
      - hermes chat -Q --yolo -q        → hermes -p my_profile chat -Q --yolo -q
      - uvx --from hermes-agent hermes chat ... → uvx ... hermes -p my_profile chat ...
      - 自定义 HERMES_CMD

    如果命令中没有 `chat` 关键字，则在第一个参数后插入。
    """
    base_arguments = hermes_process.hermes_arguments()
    try:
        chat_index = base_arguments.index("chat")
    except ValueError:
        # 没有 chat 子命令 → 在命令名之后插入（索引 1）
        chat_index = 1 if base_arguments else 0
    return [*base_arguments[:chat_index], "-p", profile_name, *base_arguments[chat_index:]]
