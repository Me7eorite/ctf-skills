"""可复用的 Hermes 子进程基础能力。

分片执行路径（`hermes.runner.HermesRunner`）和 research 路径（第 7 节新增的
`hermes.research`）共用这里的子进程逻辑。这里不依赖 `ProjectPaths`，
调用方负责准备 `arguments`、`cwd`、`environment` 和 `log_path`。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HERMES_COMMAND = "hermes chat -Q --yolo -q"
DEFAULT_HERMES_TIMEOUT = 1500
HERMES_TIMEOUT_RETURNCODE = 124


@dataclass(frozen=True)
class HermesProcessResult:
    """`invoke_capture` 的执行结果：返回码、捕获的 stdout、是否被取消。"""

    returncode: int
    stdout: str
    cancelled: bool


def hermes_arguments() -> list[str]:
    """定位 Hermes 可执行文件，并构造不含 prompt 的基础 argv。"""
    command = os.environ.get("HERMES_CMD")
    if command:
        return shlex.split(command)

    hermes = shutil.which("hermes")
    if hermes:
        return [hermes, "chat", "-Q", "--yolo", "-q"]

    uvx = shutil.which("uvx")
    python311 = Path.home() / ".local" / "bin" / "python3.11.exe"
    if uvx:
        arguments = [uvx]
        if python311.exists():
            arguments.extend(["--python", str(python311)])
        arguments.extend(
            ["--from", "hermes-agent", "hermes", "chat", "-Q", "--yolo", "-q"]
        )
        return arguments
    return shlex.split(DEFAULT_HERMES_COMMAND)


def apply_legacy_custom_provider(
    hermes_home: Path, environment: dict[str, str]
) -> bool:
    """把旧版 `model.provider=custom` 配置转换成环境变量。

    会原地修改 `environment`。当旧版配置被应用时返回 True，调用方据此把
    `--provider custom` 注入 argv。
    """
    config = hermes_home / "config.yaml"
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    model: dict[str, str] = {}
    in_model = False
    for line in lines:
        if line and not line[0].isspace():
            in_model = line.rstrip() == "model:"
            continue
        if not in_model or ":" not in line:
            continue
        key, value = line.strip().split(":", 1)
        model[key] = value.strip().strip("'\"")

    if model.get("provider") != "custom":
        return False
    if model.get("base_url"):
        environment.setdefault("CUSTOM_BASE_URL", model["base_url"])
    if model.get("api_key"):
        environment.setdefault("CUSTOM_API_KEY", model["api_key"])
    return bool(model.get("base_url"))


def remove_conflicting_custom_pool(hermes_home: Path) -> bool:
    """从 `auth.json` 的 credential pool 中移除 `custom:*` 条目。"""
    auth_path = hermes_home / "auth.json"
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    pool = payload.get("credential_pool")
    if not isinstance(pool, dict):
        return False
    filtered = {
        key: value
        for key, value in pool.items()
        if not str(key).startswith("custom:")
    }
    if len(filtered) == len(pool):
        return False
    payload["credential_pool"] = filtered
    auth_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def invoke(
    prompt: str,
    *,
    arguments: list[str],
    log_path: Path,
    cwd: Path,
    environment: dict[str, str],
    timeout: int,
) -> int:
    """把 `prompt` 作为最后一个 argv 运行 Hermes，并记录完整日志。

    这是旧版 `invoke_hermes` 的等价抽取版本。函数返回子进程返回码；
    stdout/stderr 会直接写入 `log_path`。分片执行流水线会调用它。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    full_arguments = [*arguments, prompt]
    with log_path.open("w", encoding="utf-8") as output:
        output.write(
            f"$ {' '.join(shlex.quote(arg) for arg in full_arguments[:-1])} <prompt>\n\n"
        )
        try:
            process = subprocess.run(
                full_arguments,
                cwd=cwd,
                env=environment,
                text=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            output.write(
                "Hermes command not found. Set HERMES_CMD or install Hermes.\n"
            )
            return 127
        except subprocess.TimeoutExpired:
            output.write(f"\nHermes command timed out after {timeout}s.\n")
            return HERMES_TIMEOUT_RETURNCODE
    return process.returncode


# 中文注释：只有这些环境变量会写入捕获日志头，`CUSTOM_API_KEY` 等密钥会被刻意省略。
_LOGGED_ENV_KEYS = ("HERMES_HOME", "HERMES_CMD", "HERMES_PROFILE", "CUSTOM_BASE_URL")


def invoke_capture(
    prompt: str,
    *,
    arguments: list[str],
    log_path: Path,
    cwd: Path,
    environment: dict[str, str],
    timeout: int,
    cancel_event: threading.Event | None = None,
) -> HermesProcessResult:
    """运行 Hermes，同时把 stdout 捕获到内存并镜像写入 `log_path`。

    Research Agent 会解析 Hermes 的 JSON 输出，并且在租约丢失时需要通过
    `cancel_event` 终止子进程。日志文件包含完整命令、环境摘要、stderr，
    以及用 `--- stdout ---` 到 `--- end stdout ---` 包裹的 stdout，
    方便无需重跑 Hermes 就能诊断失败。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    full_arguments = [*arguments, prompt]

    env_summary_lines = [
        f"{key}={environment[key]}" for key in _LOGGED_ENV_KEYS if key in environment
    ]
    header = (
        f"$ {' '.join(shlex.quote(arg) for arg in full_arguments[:-1])} <prompt>\n"
        f"cwd: {cwd}\n"
        f"timeout: {timeout}s\n"
        f"env:\n"
        + ("  " + "\n  ".join(env_summary_lines) + "\n" if env_summary_lines else "  (none)\n")
    )

    try:
        process = subprocess.Popen(
            full_arguments,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        log_path.write_text(
            header + "\nHermes command not found. Set HERMES_CMD or install Hermes.\n",
            encoding="utf-8",
        )
        return HermesProcessResult(returncode=127, stdout="", cancelled=False)

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain(stream, sink):
        if stream is None:
            return
        for line in stream:
            sink.append(line)

    stdout_thread = threading.Thread(
        target=_drain, args=(process.stdout, stdout_chunks), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_drain, args=(process.stderr, stderr_chunks), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    cancelled = False
    cancelled_at: str | None = None
    timed_out = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            if process.poll() is not None:
                break
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                cancelled_at = datetime.now(tz=timezone.utc).isoformat()
                _terminate(process)
                break
            if time.monotonic() > deadline:
                timed_out = True
                _terminate(process)
                break
            time.sleep(0.1)
    except BaseException:
        # 中文注释：调用方被中断时也要清理 Hermes 子进程，避免后台进程继续占用租约窗口。
        _terminate(process)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        process.wait()
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        log_path.write_text(
            header
            + "\ninterrupted before completion\n"
            + "\n--- stderr ---\n"
            + stderr
            + ("" if stderr.endswith("\n") or not stderr else "\n")
            + "--- end stderr ---\n"
            + "\n--- stdout ---\n"
            + stdout
            + ("" if stdout.endswith("\n") or not stdout else "\n")
            + "--- end stdout ---\n",
            encoding="utf-8",
        )
        raise

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    process.wait()

    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    returncode = process.returncode if process.returncode is not None else 0
    if timed_out:
        returncode = HERMES_TIMEOUT_RETURNCODE

    log_path.write_text(
        header
        + (f"\ncancelled at {cancelled_at}\n" if cancelled else "")
        + (f"\ntimed out after {timeout}s\n" if timed_out else "")
        + "\n--- stderr ---\n"
        + stderr
        + ("" if stderr.endswith("\n") or not stderr else "\n")
        + "--- end stderr ---\n"
        + "\n--- stdout ---\n"
        + stdout
        + ("" if stdout.endswith("\n") or not stdout else "\n")
        + "--- end stdout ---\n",
        encoding="utf-8",
    )

    return HermesProcessResult(returncode=returncode, stdout=stdout, cancelled=cancelled)


def profile_exists(profile_name: str) -> bool:
    """当 `hermes profile show <profile_name>` 成功退出时返回 True。

    供 `challenge-factory profile bind` 在持久化绑定前校验 profile。
    启动失败、二进制缺失、超时都统一视为不存在，让调用方只处理布尔结果。
    """
    # 中文注释：复用 Hermes 基础命令，改写为 profile show，用布尔值统一表达检查结果。
    base_arguments = hermes_arguments()
    try:
        chat_index = base_arguments.index("chat")
    except ValueError:
        chat_index = 1 if base_arguments else 0
    profile_arguments = [*base_arguments[:chat_index], "profile", "show", profile_name]
    try:
        profile_process = subprocess.run(
            profile_arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return profile_process.returncode == 0


def _terminate(process: "subprocess.Popen[str]") -> None:
    """尽力终止子进程：先 SIGTERM，等待 5 秒后再 SIGKILL。"""
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
