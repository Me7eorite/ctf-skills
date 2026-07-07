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
    cwd: Path | None = None,
) -> HermesProcessResult:
    """使用指定 profile 运行 Hermes Design Agent，并捕获 stdout 和日志。

    与 invoke_research_agent 的区别:
      - 不使用 cancel_event（设计流程暂不支持外部取消）
      - 返回 HermesProcessResult（包含 returncode/stdout/cancelled 标志）
    """
    # 组装带 profile 的 Hermes 命令参数
    hermes_arguments = hermes_process.inject_profile_argument(profile_name)

    # 准备环境变量：设置 HERMES_HOME（如果尚未设置）
    environment_map = os.environ.copy()
    template_home = hermes_process.resolve_template_hermes_home(
        paths.hermes_home,
        environment_map,
    )

    # 处理旧版 custom provider 兼容逻辑
    if hermes_process.apply_legacy_custom_provider(template_home, environment_map):
        # 删除冲突的 custom pool 配置
        hermes_process.remove_conflicting_custom_pool(template_home)
        # 在 -q 标志前插入 --provider custom
        query_flag_index = (
            hermes_arguments.index("-q") if "-q" in hermes_arguments else len(hermes_arguments)
        )
        hermes_arguments[query_flag_index:query_flag_index] = ["--provider", "custom"]

    invoke_cwd = cwd or paths.root
    if cwd is not None:
        hermes_process.configure_shared_hermes_home(
            environment_map,
            source_home=template_home,
            shared_home=_shared_design_hermes_home(paths, invoke_cwd),
            profile_name=profile_name,
        )
        terminal_backend = hermes_process.effective_terminal_backend(
            paths.hermes_home,
            environment_map,
            profile_name=profile_name,
            allow_cli_fallback=False,
        )
        hermes_process.configure_terminal_workspace(
            environment_map,
            cwd=invoke_cwd,
            terminal_backend=terminal_backend,
        )
        environment_map.setdefault("TERMINAL_CWD", str(invoke_cwd))
    elif (
        hermes_process.project_hermes_home_is_configured(paths.hermes_home)
        and not environment_map.get("HERMES_HOME")
    ):
        environment_map["HERMES_HOME"] = str(paths.hermes_home)

    # 委托通用捕获执行器运行
    return invoke_capture(
        prompt,
        arguments=hermes_arguments,
        log_path=log_path,
        cwd=invoke_cwd,
        environment=environment_map,
        timeout=timeout,
    )


def _shared_design_hermes_home(paths: ProjectPaths, cwd: Path) -> Path:
    del cwd
    return paths.root / "work" / "design" / "hermes-home"
