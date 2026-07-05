"""可复用的 Hermes 子进程基础能力。

分片执行路径（`hermes.runner.HermesRunner`）和 research 路径（第 7 节新增的
`hermes.research`）共用这里的子进程逻辑。这里不依赖 `ProjectPaths`，
调用方负责准备 `arguments`、`cwd`、`environment` 和 `log_path`。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.build_timeout import AttemptDeadlineExceeded, bounded_hermes_timeout
from core.clock import beijing_now_isoformat
from core.jsonio import read_json, write_json

DEFAULT_HERMES_COMMAND = "hermes chat -Q --yolo -q"
# -Q: 查询模式（单次问答，非交互）；--yolo: 自动批准所有工具调用；-q: 静默模式
DEFAULT_HERMES_TIMEOUT = 1500  # 默认超时秒数（25 分钟）
HERMES_TIMEOUT_RETURNCODE = 124  # 超时返回码（与 timeout 命令兼容）
TERMINATION_WAIT_TIMEOUT = 10
_ERROR_MARKER_MAX_BYTES = 64 * 1024
_CTF_HERMES_LABEL_KEY = "ctf-skills-hermes-run"
_CTF_HERMES_OWNER_LABEL_KEY = "ctf-skills-owner"
_CTF_HERMES_LABEL_ENV = "CTF_SKILLS_HERMES_DOCKER_LABEL"
# The probe starts a full Hermes CLI process. On server-side custom providers,
# plugin loading plus model metadata probing can take over a minute before the
# first terminal/file tool call is made, so keep this comfortably above that
# startup cost while still failing much earlier than a normal worker timeout.
TERMINAL_WORKSPACE_PROBE_TIMEOUT = 240


class TerminalWorkspaceVisibilityError(RuntimeError):
    """Raised when a container terminal cannot write to the host workspace."""


@dataclass(frozen=True)
class HermesProcessResult:
    """`invoke_capture` 的执行结果：返回码、捕获的 stdout、是否被取消。"""

    returncode: int
    stdout: str
    cancelled: bool


NUL_SANITIZED_MESSAGE = "prompt contained NUL bytes; sanitized before Hermes invocation"
NUL_BLOCKED_MESSAGE = "Hermes invocation contained embedded NUL byte and was blocked"


def sanitize_prompt_text(text: str) -> str:
    """Replace real NUL bytes with printable text before Hermes invocation/logging."""
    return re.sub(r"\\u0000", r"\\x00", text.replace("\x00", "\\x00"), flags=re.IGNORECASE)


def _sanitize_invocation_values(values: list[str]) -> tuple[list[str], bool]:
    sanitized = [sanitize_prompt_text(str(value)) for value in values]
    return sanitized, sanitized != values


def _sanitize_invocation_environment(
    environment: dict[str, str],
) -> tuple[dict[str, str], bool]:
    sanitized = {
        sanitize_prompt_text(str(key)): sanitize_prompt_text(str(value))
        for key, value in environment.items()
    }
    return sanitized, sanitized != environment


def hermes_arguments() -> list[str]:
    """定位 Hermes 可执行文件，并构造不含 prompt 的基础 argv。"""
    command = os.environ.get("HERMES_CMD")
    if command:
        return shlex.split(command)

    hermes = shutil.which("hermes")
    if hermes:
        return [hermes, "chat", "-Q", "--yolo", "-q"]

    for candidate in _hermes_executable_candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate), "chat", "-Q", "--yolo", "-q"]

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


def _hermes_executable_candidates() -> list[Path]:
    """Common non-login-shell locations for the Hermes CLI.

    Dashboard services often run with a minimal PATH, so user shell managers
    such as pyenv/asdf/mise are invisible unless we probe their standard shim
    directories. Operators can extend this without code changes via
    HERMES_BIN_DIR or HERMES_EXTRA_PATHS.
    """
    home = Path.home()
    candidates: list[Path] = []
    bin_dir = os.environ.get("HERMES_BIN_DIR")
    if bin_dir:
        candidates.append(Path(bin_dir).expanduser() / "hermes")
    extra_paths = os.environ.get("HERMES_EXTRA_PATHS", "")
    for raw_path in extra_paths.split(os.pathsep):
        if raw_path:
            candidates.append(Path(raw_path).expanduser() / "hermes")
    candidates.extend(
        [
            home / ".pyenv" / "shims" / "hermes",
            home / ".local" / "bin" / "hermes",
            home / ".asdf" / "shims" / "hermes",
            home / ".nix-profile" / "bin" / "hermes",
            home / ".cargo" / "bin" / "hermes",
            home / ".npm-global" / "bin" / "hermes",
            home / ".bun" / "bin" / "hermes",
            home / ".local" / "share" / "mise" / "shims" / "hermes",
        ]
    )
    seen: set[str] = set()
    unique = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def inject_profile_argument(profile_name: str) -> list[str]:
    """Insert ``-p <profile>`` before the Hermes ``chat`` subcommand."""
    base_arguments = hermes_arguments()
    try:
        chat_index = base_arguments.index("chat")
    except ValueError:
        chat_index = 1 if base_arguments else 0
    return [
        *base_arguments[:chat_index],
        "-p",
        profile_name,
        *base_arguments[chat_index:],
    ]


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


def project_hermes_home_is_configured(hermes_home: Path) -> bool:
    """Return true when a project Hermes home has actual runtime configuration."""
    return (
        (hermes_home / "config.yaml").is_file()
        or (hermes_home / "auth.json").is_file()
        or (hermes_home / "profiles").is_dir()
    )


def effective_terminal_backend(
    hermes_home: Path,
    environment: dict[str, str] | None = None,
    *,
    profile_name: str | None = None,
    allow_cli_fallback: bool = True,
) -> str | None:
    """Return the configured Hermes terminal backend, if it can be determined.

    ``TERMINAL_ENV`` wins over profile/project ``.env`` and ``config.yaml`` in
    Hermes. A missing backend is intentionally reported as ``None`` so callers
    can fail closed for high-risk workloads.
    """
    env = os.environ if environment is None else environment
    raw_env_backend = env.get("TERMINAL_ENV")
    if raw_env_backend and raw_env_backend.strip():
        return raw_env_backend.strip().lower()

    configured_home = hermes_home
    raw_hermes_home = env.get("HERMES_HOME")
    if raw_hermes_home and raw_hermes_home.strip():
        configured_home = Path(raw_hermes_home.strip()).expanduser()
    elif not project_hermes_home_is_configured(hermes_home):
        configured_home = Path.home() / ".hermes"

    if profile_name:
        profile_home = configured_home / "profiles" / profile_name
        profile_dotenv_backend = _terminal_env_from_dotenv(profile_home / ".env")
        if profile_dotenv_backend:
            return profile_dotenv_backend
        profile_config_backend = _terminal_backend_from_config(profile_home / "config.yaml")
        if profile_config_backend:
            return profile_config_backend

    dotenv_backend = _terminal_env_from_dotenv(configured_home / ".env")
    if dotenv_backend:
        return dotenv_backend

    config_backend = _terminal_backend_from_config(configured_home / "config.yaml")
    if config_backend:
        return config_backend
    if profile_name and allow_cli_fallback:
        cli_backend = _terminal_backend_from_cli(profile_name)
        if cli_backend:
            return cli_backend
    return None


def configure_terminal_workspace(
    environment: dict[str, str],
    *,
    cwd: Path,
    terminal_backend: str | None,
    docker_cleanup_label: str | None = None,
) -> None:
    """Align Hermes terminal tool cwd with the host-owned execution workspace.

    ``subprocess.run(cwd=...)`` only controls the outer Hermes CLI process. For
    container backends, Hermes terminal/file tools have their own runtime cwd.
    The Docker backend treats host paths under ``/root`` as container-internal,
    so per-attempt cwd passthrough is unreliable there. Mount the stable
    ``work/executions`` root and point the container cwd at the matching path.
    """
    backend = terminal_backend.strip().lower() if isinstance(terminal_backend, str) else ""
    if backend != "docker":
        return
    container_cwd, volumes = _docker_workspace_mapping(cwd)
    environment["_HERMES_GATEWAY"] = "1"
    environment["TERMINAL_CWD"] = container_cwd
    environment["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"] = "1"
    environment["TERMINAL_DOCKER_VOLUMES"] = json.dumps(volumes)
    # A persisted Hermes docker terminal container can keep the mount set from a
    # previous attempt. Build workspaces are per-attempt, so force the backend to
    # start with the current cwd/mount contract instead of reusing stale state.
    environment["TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES"] = "false"
    label = docker_cleanup_label or docker_invocation_label(cwd)
    environment[_CTF_HERMES_LABEL_ENV] = label
    _append_docker_extra_args(
        environment,
        [
            "--label",
            f"{_CTF_HERMES_OWNER_LABEL_KEY}=ctf-skills",
            "--label",
            f"{_CTF_HERMES_LABEL_KEY}={label}",
        ],
    )


def docker_invocation_label(cwd: Path) -> str:
    """Return a Docker-label-safe identifier for one Hermes terminal run."""
    attempt = _attempt_id_from_execution_cwd(cwd)
    raw = f"{attempt or 'workspace'}-{os.getpid()}-{uuid.uuid4().hex[:10]}"
    return _sanitize_docker_label(raw)[:63] or uuid.uuid4().hex[:12]


def _attempt_id_from_execution_cwd(cwd: Path) -> str | None:
    parts = cwd.resolve().parts
    for index in range(len(parts) - 2):
        if parts[index] == "work" and parts[index + 1] == "executions":
            return parts[index + 2]
    return None


def _append_docker_extra_args(environment: dict[str, str], extra_args: list[str]) -> None:
    current: list[Any]
    raw = environment.get("TERMINAL_DOCKER_EXTRA_ARGS")
    if raw:
        try:
            parsed = json.loads(raw)
            current = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            current = []
    else:
        current = []
    merged = [str(item) for item in current if isinstance(item, str)]
    merged.extend(extra_args)
    environment["TERMINAL_DOCKER_EXTRA_ARGS"] = json.dumps(merged)


def _docker_workspace_mapping(cwd: Path) -> tuple[str, list[str]]:
    resolved = cwd.resolve()
    parts = resolved.parts
    for index in range(len(parts) - 1):
        if parts[index] == "work" and parts[index + 1] == "executions":
            host_root = Path(*parts[: index + 2])
            relative = resolved.relative_to(host_root).as_posix()
            container_root = "/workspace/executions"
            return f"{container_root}/{relative}", [f"{host_root}:{container_root}"]
    return "/workspace", [f"{resolved}:/workspace"]


def verify_terminal_workspace_visibility(
    *,
    arguments: list[str],
    log_path: Path,
    cwd: Path,
    environment: dict[str, str],
    terminal_backend: str | None,
    timeout: int = TERMINAL_WORKSPACE_PROBE_TIMEOUT,
) -> None:
    """Fail fast when the Hermes terminal backend cannot see the host cwd.

    Hermes CLI stdout/stderr is host-captured, so logs can exist even when
    terminal/file tools write into a private container filesystem. The runner
    publishes only from the host-owned execution workspace; we therefore prove
    visibility with a tiny marker before the expensive build prompt runs.
    """
    backend = terminal_backend.strip().lower() if isinstance(terminal_backend, str) else ""
    probe_timeout = min(timeout, TERMINAL_WORKSPACE_PROBE_TIMEOUT)
    marker = cwd / "state" / "terminal-workspace-probe.json"
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise TerminalWorkspaceVisibilityError(
            f"cannot reset terminal workspace probe marker: {exc}"
        ) from exc
    prompt = (
        "This is a filesystem visibility probe. Do not inspect other files. "
        "Create or overwrite exactly ./state/terminal-workspace-probe.json "
        "with this JSON object and then stop: "
        '{"ok":true,"probe":"terminal_workspace"}'
    )
    probe_log = log_path.with_name(log_path.name + ".terminal_probe.log")
    returncode = _invoke_terminal_workspace_probe(
        prompt=prompt,
        arguments=arguments,
        log_path=probe_log,
        cwd=cwd,
        environment=environment,
        timeout=probe_timeout,
    )
    if returncode != 0:
        if backend == "docker":
            cleanup = cleanup_hermes_terminal_containers(arguments=arguments)
            _append_probe_recovery_log(probe_log, cleanup)
            raise TerminalWorkspaceVisibilityError(
                "Hermes terminal workspace probe failed after cleaning stale "
                f"Docker terminal containers (return code {returncode}); see {probe_log}"
            )
        else:
            raise TerminalWorkspaceVisibilityError(
                f"Hermes terminal workspace probe failed with return code {returncode}; "
                f"see {probe_log}"
            )
    if not marker.is_file():
        if backend == "docker":
            cleanup = cleanup_hermes_terminal_containers(arguments=arguments)
            _append_probe_recovery_log(probe_log, cleanup)
            backend_label = backend or "unknown"
            env_detail = (
                f"TERMINAL_CWD={environment.get('TERMINAL_CWD', '')!r}, "
                "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE="
                f"{environment.get('TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE', '')!r}, "
                "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES="
                f"{environment.get('TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES', '')!r}"
            )
            raise TerminalWorkspaceVisibilityError(
                f"Hermes terminal backend ({backend_label}) did not write to the host execution workspace "
                f"after stale-container cleanup. cwd={cwd}; expected marker={marker}; {env_detail}."
            )
        else:
            backend_label = backend or "unknown"
            env_detail = (
                f"TERMINAL_CWD={environment.get('TERMINAL_CWD', '')!r}, "
                "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE="
                f"{environment.get('TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE', '')!r}, "
                "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES="
                f"{environment.get('TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES', '')!r}"
            )
            raise TerminalWorkspaceVisibilityError(
                f"Hermes terminal backend ({backend_label}) did not write to the host execution workspace. "
                f"cwd={cwd}; expected marker={marker}; {env_detail}. "
                "It likely wrote inside the container private cwd such as /home/hermes. "
                "Stop stale hermes-* docker containers and ensure the backend starts "
                "with TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE=1, or use a terminal "
                "backend that writes directly to the host workspace."
            )
    try:
        parsed = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TerminalWorkspaceVisibilityError(
            f"Hermes terminal workspace probe marker is unreadable: {exc}"
        ) from exc
    if not isinstance(parsed, dict) or parsed.get("ok") is not True:
        raise TerminalWorkspaceVisibilityError(
            "Hermes terminal workspace probe marker has unexpected content"
        )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as output:
            backend_label = backend or "unknown"
            output.write(
                "terminal workspace probe passed "
                f"(backend={backend_label}, marker={marker})\n"
            )
    except OSError:
        pass
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise TerminalWorkspaceVisibilityError(
            f"cannot remove terminal workspace probe marker: {exc}"
        ) from exc


def _invoke_terminal_workspace_probe(
    *,
    prompt: str,
    arguments: list[str],
    log_path: Path,
    cwd: Path,
    environment: dict[str, str],
    timeout: int,
) -> int:
    return invoke(
        prompt,
        arguments=arguments,
        log_path=log_path,
        cwd=cwd,
        environment=environment,
        timeout=timeout,
    )


def cleanup_invocation_hermes_containers(*, environment: dict[str, str]) -> str:
    """Remove Hermes Docker terminal containers created for one invocation."""
    label = environment.get(_CTF_HERMES_LABEL_ENV, "").strip()
    if not label:
        return "skipped Hermes Docker invocation cleanup: invocation label is absent"
    docker = shutil.which("docker")
    if not docker:
        return "skipped Hermes Docker invocation cleanup: docker is unavailable"
    filters = [
        "--filter",
        f"label={_CTF_HERMES_OWNER_LABEL_KEY}=ctf-skills",
        "--filter",
        f"label={_CTF_HERMES_LABEL_KEY}={_sanitize_docker_label(label)}",
    ]
    try:
        ps = subprocess.run(
            [docker, "ps", "-aq", *filters],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Hermes Docker invocation cleanup skipped: docker ps failed: {exc}"
    if ps.returncode != 0:
        stderr = getattr(ps, "stderr", "") or ""
        return f"Hermes Docker invocation cleanup skipped: docker ps exited {ps.returncode}: {stderr.strip()}"
    stdout = getattr(ps, "stdout", "") or ""
    ids = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not ids:
        return f"Hermes Docker invocation cleanup found no containers for label {label!r}"
    try:
        rm = subprocess.run(
            [docker, "rm", "-f", *ids],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Hermes Docker invocation cleanup failed: docker rm failed: {exc}"
    if rm.returncode != 0:
        return f"Hermes Docker invocation cleanup failed: docker rm exited {rm.returncode}: {rm.stderr.strip()}"
    return f"removed {len(ids)} Hermes Docker terminal container(s) for invocation label {label!r}: {', '.join(ids)}"


def cleanup_hermes_terminal_containers(*, arguments: list[str]) -> str:
    """Remove Hermes-managed Docker terminal containers for the active profile.

    This is deliberately narrow: only containers labeled both
    ``hermes-agent=1`` and the current ``hermes-profile`` are removed. It is
    used after a probe timeout because a stuck terminal process can otherwise
    poison subsequent retries for the same profile.
    """

    profile = _profile_from_arguments(arguments)
    if not profile:
        return "skipped stale Hermes Docker cleanup: profile is unknown"
    docker = shutil.which("docker")
    if not docker:
        return "skipped stale Hermes Docker cleanup: docker is unavailable"
    filters = [
        "--filter",
        "label=hermes-agent=1",
        "--filter",
        f"label=hermes-profile={_sanitize_docker_label(profile)}",
    ]
    try:
        ps = subprocess.run(
            [docker, "ps", "-aq", *filters],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"stale Hermes Docker cleanup skipped: docker ps failed: {exc}"
    if ps.returncode != 0:
        stderr = getattr(ps, "stderr", "") or ""
        return f"stale Hermes Docker cleanup skipped: docker ps exited {ps.returncode}: {stderr.strip()}"
    stdout = getattr(ps, "stdout", "") or ""
    ids = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not ids:
        return f"stale Hermes Docker cleanup found no containers for profile {profile!r}"
    try:
        rm = subprocess.run(
            [docker, "rm", "-f", *ids],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"stale Hermes Docker cleanup failed: docker rm failed: {exc}"
    if rm.returncode != 0:
        return f"stale Hermes Docker cleanup failed: docker rm exited {rm.returncode}: {rm.stderr.strip()}"
    return f"removed {len(ids)} stale Hermes Docker terminal container(s) for profile {profile!r}: {', '.join(ids)}"


def _profile_from_arguments(arguments: list[str]) -> str | None:
    for index, value in enumerate(arguments):
        if value in {"-p", "--profile"} and index + 1 < len(arguments):
            profile = arguments[index + 1].strip()
            return profile or None
        if value.startswith("--profile="):
            profile = value.split("=", 1)[1].strip()
            return profile or None
    return None


def _sanitize_docker_label(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    return sanitized or "default"


def _append_probe_recovery_log(path: Path, message: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[probe-recovery] {message}\n")
    except OSError:
        pass

def _terminal_env_from_dotenv(dotenv_path: Path) -> str | None:
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() != "TERMINAL_ENV":
            continue
        backend = value.strip().strip("'\"")
        return backend.lower() or None
    return None


def _terminal_backend_from_config(config_path: Path) -> str | None:
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    in_terminal = False
    terminal_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if not line[0].isspace():
            in_terminal = stripped == "terminal:"
            terminal_indent = indent if in_terminal else None
            continue
        if not in_terminal:
            continue
        if terminal_indent is not None and indent <= terminal_indent:
            in_terminal = False
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        if key.strip() != "backend":
            continue
        backend = value.strip().strip("'\"")
        return backend.lower() or None
    return None


def _terminal_backend_from_cli(profile_name: str) -> str | None:
    """Read the effective terminal backend from `hermes -p <profile> config show`.

    This keeps Challenge Factory aligned with Hermes' own profile resolution:
    a profile may live in the user's default Hermes home even when the project
    does not have a `.hermes/profiles/<name>/config.yaml` mirror.
    """
    base_arguments = hermes_arguments()
    try:
        chat_index = base_arguments.index("chat")
    except ValueError:
        chat_index = 1 if base_arguments else 0
    config_arguments = [
        *base_arguments[:chat_index],
        "-p",
        profile_name,
        "config",
        "show",
    ]
    try:
        config_process = subprocess.run(
            config_arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if config_process.returncode != 0:
        return None
    return _terminal_backend_from_config_show(config_process.stdout)


def _terminal_backend_from_config_show(output: str) -> str | None:
    in_terminal = False
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        normalized = stripped.lstrip("◆").strip()
        if normalized.lower() == "terminal":
            in_terminal = True
            continue
        if normalized.endswith(":") and normalized[:-1].strip().lower() == "terminal":
            in_terminal = True
            continue
        if in_terminal and ":" in normalized:
            key, value = normalized.split(":", 1)
            if key.strip().lower() == "backend":
                backend = value.strip().strip("'\"")
                return backend.lower() or None
        if in_terminal and normalized and ":" not in normalized and normalized.lower() != "terminal":
            continue
    return None


def invoke(
    prompt: str,
    *,
    arguments: list[str],
    log_path: Path,
    cwd: Path,
    environment: dict[str, str],
    timeout: int | float,
    attempt_deadline: float | None = None,
    profile_log_path: Path | None = None,
) -> int:
    """把 sanitized `prompt` 作为最后一个 argv 运行 Hermes，并记录完整日志。

    这是旧版 `invoke_hermes` 的等价抽取版本。函数返回子进程返回码；
    stdout/stderr 会直接写入 `log_path`。

    与 invoke_capture 的区别:
      - stdout 不捕获到内存，直接写入日志文件（节省内存）
      - stderr 合并到 stdout（stderr=subprocess.STDOUT）
      - 不支持 cancel_event（不能外部取消）
      - 适用于分片执行流水线（不需要解析 stdout 的场景）
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sanitized_prompt = sanitize_prompt_text(prompt)
    prompt_sanitized = sanitized_prompt != prompt
    safe_arguments, arguments_sanitized = _sanitize_invocation_values(arguments)
    safe_environment, environment_sanitized = _sanitize_invocation_environment(environment)
    full_arguments = [*safe_arguments, sanitized_prompt]
    returncode: int
    effective_timeout = bounded_hermes_timeout(timeout, attempt_deadline)
    with log_path.open("w", encoding="utf-8") as output:
        # 日志头：显示命令（prompt 用 <prompt> 占位避免泄露）
        output.write(
            f"$ {' '.join(shlex.quote(arg) for arg in full_arguments[:-1])} <prompt>\n"
            f"cwd: {cwd}\n"
            f"timeout: {effective_timeout}s\n"
        )
        if prompt_sanitized:
            output.write(f"{NUL_SANITIZED_MESSAGE}\n")
        if arguments_sanitized or environment_sanitized:
            output.write("Hermes invocation argv/env contained NUL bytes; sanitized before launch\n")
        if profile_log_path is not None:
            output.write(f"profile_log: {profile_log_path}\n")
        output.write("\n")
        output.flush()
        tail_stop = threading.Event()
        tail_thread = _start_profile_log_mirror(
            profile_log_path,
            output,
            stop_event=tail_stop,
        )
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                full_arguments,
                cwd=cwd,
                env=safe_environment,
                stdin=subprocess.DEVNULL,
                text=True,
                stdout=output,           # stdout 直接写入日志文件
                stderr=subprocess.STDOUT, # stderr 合并到 stdout
                start_new_session=os.name != "nt",
            )
            returncode = process.wait(timeout=effective_timeout)
        except (FileNotFoundError, PermissionError):
            output.write(
                "Hermes command not found or not executable. Set HERMES_CMD or install Hermes.\n"
            )
            returncode = 127  # 标准 POSIX 返回码：命令未找到
        except ValueError as exc:
            if "embedded null byte" not in str(exc):
                raise
            output.write(f"{NUL_BLOCKED_MESSAGE}: {exc}\n")
            returncode = 1
        except subprocess.TimeoutExpired:
            if process is not None:
                killed_process_group = _terminate(process)
                _wait_after_terminate(process)
            output.write(f"\nHermes command timed out after {effective_timeout}s.\n")
            if attempt_deadline is not None:
                output.write("timeout_kind: attempt_deadline\n")
            if process is not None:
                output.write(f"killed_process_group: {str(killed_process_group).lower()}\n")
            returncode = HERMES_TIMEOUT_RETURNCODE
        except BaseException:
            if process is not None:
                _terminate(process)
                _wait_after_terminate(process)
            raise
        else:
            returncode = returncode if returncode is not None else 0
        finally:
            tail_stop.set()
            if tail_thread is not None:
                tail_thread.join(timeout=2)
            cleanup = cleanup_invocation_hermes_containers(environment=safe_environment)
            if cleanup and not cleanup.startswith("skipped"):
                output.write(f"\n[hermes-docker-cleanup] {cleanup}\n")
            output.flush()
    _write_error_marker_from_log(log_path)
    marker = read_json(_error_marker_path(log_path), None)
    if returncode != 0 and _marker_is_rate_limit(marker):
        retry_depth = _env_int(environment, "_HERMES_RATE_LIMIT_RETRY_DEPTH", 0)
        max_retries = _env_int(environment, "HERMES_RATE_LIMIT_RETRIES", 2)
        if retry_depth < max_retries:
            try:
                retry_timeout = bounded_hermes_timeout(timeout, attempt_deadline)
            except AttemptDeadlineExceeded:
                try:
                    with log_path.open("a", encoding="utf-8") as output:
                        output.write(
                            "\n[hermes-rate-limit-retry] global deadline exceeded; "
                            "retry skipped\n"
                        )
                except OSError:
                    pass
                return HERMES_TIMEOUT_RETURNCODE
            retry_log = log_path.with_name(f"{log_path.name}.retry{retry_depth + 1}.log")
            try:
                log_path.replace(retry_log)
            except OSError:
                retry_log = log_path
            delay = min(30.0, 0.25 * (2 ** retry_depth))
            remaining_after_delay = retry_timeout - delay if attempt_deadline is not None else None
            if remaining_after_delay is not None and remaining_after_delay <= 0:
                try:
                    with log_path.open("a", encoding="utf-8") as output:
                        output.write(
                            "\n[hermes-rate-limit-retry] global deadline exceeded; "
                            "retry skipped\n"
                        )
                except OSError:
                    pass
                return HERMES_TIMEOUT_RETURNCODE
            time.sleep(delay)
            retry_env = dict(environment)
            retry_env["_HERMES_RATE_LIMIT_RETRY_DEPTH"] = str(retry_depth + 1)
            retry_returncode = invoke(
                prompt,
                arguments=arguments,
                log_path=log_path,
                cwd=cwd,
                environment=retry_env,
                timeout=retry_timeout,
                attempt_deadline=attempt_deadline,
                profile_log_path=profile_log_path,
            )
            try:
                with log_path.open("a", encoding="utf-8") as output:
                    output.write(
                        "\n[hermes-rate-limit-retry] "
                        f"attempt={retry_depth + 1} previous_log={retry_log} "
                        f"delay={delay:.2f}s\n"
                    )
            except OSError:
                pass
            return retry_returncode
    return returncode


def _env_int(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name, os.environ.get(name))
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _marker_is_rate_limit(marker: Any) -> bool:
    if not isinstance(marker, dict):
        return False
    status = str(marker.get("status_code") or "")
    error_type = str(marker.get("error_type") or marker.get("type") or "").lower()
    return status == "429" or "rate_limit" in error_type or "overloaded" in error_type


def _start_profile_log_mirror(
    profile_log_path: Path | None,
    output,
    *,
    stop_event: threading.Event,
) -> threading.Thread | None:
    """Mirror new Hermes profile log lines into the per-attempt log while running."""
    if profile_log_path is None:
        return None

    def _mirror() -> None:
        offset = 0
        try:
            offset = profile_log_path.stat().st_size
        except OSError:
            pass
        while not stop_event.wait(1.0):
            try:
                with profile_log_path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
            except OSError:
                continue
            if not chunk:
                continue
            for line in chunk.splitlines(True):
                output.write("[profile] " + line)
            output.flush()

    thread = threading.Thread(target=_mirror, daemon=True)
    thread.start()
    return thread


def _write_error_marker_from_log(log_path: Path) -> None:
    marker = _detect_error_marker(_tail_text(log_path, _ERROR_MARKER_MAX_BYTES))
    marker_path = _error_marker_path(log_path)
    if marker is None:
        try:
            marker_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        return
    write_json(marker_path, marker)


def _error_marker_path(log_path: Path) -> Path:
    return log_path.with_name(log_path.name + ".error_marker.json")


def _tail_text(path: Path, limit: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            data = handle.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _detect_error_marker(text: str) -> dict[str, Any] | None:
    marker = _detect_json_error_marker(text)
    if marker is not None:
        return marker
    lower = text.lower()
    if (
        "authentication_error" in lower
        or "anthropic 401" in lower
        or "gic密钥" in text
        or "密钥已失效" in text
    ):
        return {"type": "error", "error_type": "authentication_error", "status_code": 401, "source": "log_tail"}
    if "rate_limit_error" in lower or "overloaded_error" in lower or "rate limit" in lower:
        return {"type": "error", "error_type": "rate_limit_error", "source": "log_tail"}
    if "429" in lower and any(
        needle in lower for needle in ("anthropic", "gateway", "provider", "api")
    ):
        return {"type": "error", "error_type": "rate_limit_error", "status_code": 429, "source": "log_tail"}
    return None


def _detect_json_error_marker(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        error = parsed.get("error")
        if isinstance(error, dict):
            error_type = error.get("type") or error.get("error_type") or error.get("code")
            status_code = error.get("status_code") or parsed.get("status_code")
            if error_type or status_code:
                marker: dict[str, Any] = {"type": "error", "source": "hermes_sdk"}
                if error_type is not None:
                    marker["error_type"] = str(error_type)
                if status_code is not None:
                    marker["status_code"] = status_code
                return marker
        error_type = parsed.get("error_type") or parsed.get("type") or parsed.get("code")
        status_code = parsed.get("status_code")
        if error_type in {
            "authentication_error",
            "rate_limit_error",
            "overloaded_error",
        } or status_code in {401, 429, "401", "429"}:
            marker = {"type": "error", "source": "hermes_sdk"}
            if error_type is not None:
                marker["error_type"] = str(error_type)
            if status_code is not None:
                marker["status_code"] = status_code
            return marker
    return None


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
    sanitized_prompt = sanitize_prompt_text(prompt)
    prompt_sanitized = sanitized_prompt != prompt
    safe_arguments, arguments_sanitized = _sanitize_invocation_values(arguments)
    safe_environment, environment_sanitized = _sanitize_invocation_environment(environment)
    full_arguments = [*safe_arguments, sanitized_prompt]

    env_summary_lines = [
        f"{key}={safe_environment[key]}" for key in _LOGGED_ENV_KEYS if key in safe_environment
    ]
    header = (
        f"$ {' '.join(shlex.quote(arg) for arg in full_arguments[:-1])} <prompt>\n"
        f"cwd: {cwd}\n"
        f"timeout: {timeout}s\n"
        f"env:\n"
        + ("  " + "\n  ".join(env_summary_lines) + "\n" if env_summary_lines else "  (none)\n")
    )
    if prompt_sanitized:
        header += f"{NUL_SANITIZED_MESSAGE}\n"
    if arguments_sanitized or environment_sanitized:
        header += "Hermes invocation argv/env contained NUL bytes; sanitized before launch\n"

    # 启动 Hermes 子进程（使用 Popen 而非 run，以便同时捕获 stdout+stderr）
    try:
        process = subprocess.Popen(
            full_arguments,
            cwd=cwd,
            env=safe_environment,
            stdin=subprocess.DEVNULL,     # 关闭 stdin
            stdout=subprocess.PIPE,       # 捕获 stdout
            stderr=subprocess.PIPE,       # 捕获 stderr（与 stdout 分开）
            text=True,
            encoding="utf-8",
            start_new_session=os.name != "nt",
            errors="replace",             # 编码错误用替换字符，不崩溃
        )
    except (FileNotFoundError, PermissionError):
        log_path.write_text(
            header + "\nHermes command not found or not executable. Set HERMES_CMD or install Hermes.\n",
            encoding="utf-8",
        )
        return HermesProcessResult(returncode=127, stdout="", cancelled=False)
    except ValueError as exc:
        if "embedded null byte" not in str(exc):
            raise
        log_path.write_text(
            header + f"\n{NUL_BLOCKED_MESSAGE}: {exc}\n",
            encoding="utf-8",
        )
        return HermesProcessResult(returncode=1, stdout="", cancelled=False)

    stdout_chunks: list[str] = []  # 捕获的 stdout 行
    stderr_chunks: list[str] = []  # 捕获的 stderr 行

    def _drain(stream, sink):
        """从子进程输出流中逐行读取，追加到 sink 列表。

        在独立线程中运行，避免阻塞主线程的轮询循环。
        """
        if stream is None:
            return
        for line in stream:
            sink.append(line)

    # 启动两个 daemon 线程分别读取 stdout 和 stderr
    # daemon=True 确保主线程退出时这些线程也会自动退出
    stdout_thread = threading.Thread(
        target=_drain, args=(process.stdout, stdout_chunks), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_drain, args=(process.stderr, stderr_chunks), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    cancelled = False          # 是否被外部取消
    cancelled_at: str | None = None  # 取消时刻的 ISO 时间戳
    timed_out = False          # 是否超时
    deadline = time.monotonic() + timeout  # 超时截止时间
    try:
        # 轮询循环：每 0.1 秒检查一次进程是否结束/被取消/超时
        while True:
            if process.poll() is not None:
                break  # 进程正常结束
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                cancelled_at = beijing_now_isoformat()
                _terminate(process)  # SIGTERM → 5s → SIGKILL
                break
            if time.monotonic() > deadline:
                timed_out = True
                _terminate(process)  # SIGTERM → 5s → SIGKILL
                break
            time.sleep(0.1)  # 100ms 轮询间隔
    except BaseException:
        # 中文注释：调用方被中断时也要清理 Hermes 子进程，避免后台进程继续占用租约窗口。
        _terminate(process)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        _wait_after_terminate(process)
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

    # 等待 drain 线程完成（最多等 2 秒，防止永久阻塞）
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    # 等待子进程完全终止
    process.wait()

    # 收集捕获的输出
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    # 安全获取返回码（process.returncode 在 wait() 后不应为 None，防御性编程）
    returncode = process.returncode if process.returncode is not None else 0
    if timed_out:
        returncode = HERMES_TIMEOUT_RETURNCODE  # 覆写为超时返回码

    # 写入完整日志文件（命令头 + 状态 + stderr + stdout）
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


def hermes_profile_health(profile_name: str) -> tuple[bool, str, str]:
    """Offline sanity check for a Hermes profile used by build workers."""
    profile_dir = Path("~/.hermes/profiles").expanduser() / profile_name
    if not profile_dir.is_dir():
        return (
            False,
            "hermes_profile_missing",
            f"Hermes Profile {profile_name} 不存在，请先创建或绑定该构建 Profile",
        )
    if not profile_exists(profile_name):
        return (
            False,
            "hermes_profile_cli_unavailable",
            f"Hermes CLI 无法读取 Profile {profile_name}",
        )
    return True, "", f"Hermes Profile {profile_name} 可用"


def _read_profile_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _terminate(process: "subprocess.Popen[str]") -> bool:
    """尽力终止子进程：先 SIGTERM，等待 5 秒后再 SIGKILL。"""
    killed_process_group = False
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
            killed_process_group = True
        else:
            process.terminate()
    except ProcessLookupError:
        return killed_process_group
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
                killed_process_group = True
            except ProcessLookupError:
                return killed_process_group
        else:
            process.kill()
    return killed_process_group


def _wait_after_terminate(process: "subprocess.Popen[str]") -> None:
    try:
        process.wait(timeout=TERMINATION_WAIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return
