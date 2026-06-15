"""Reusable Hermes subprocess primitives.

Shared between the shard-execution path (`hermes.runner.HermesRunner`) and
the research path (`hermes.research` added in Section 7). Decoupled from
`ProjectPaths` — callers prepare their own `arguments`, `cwd`, `environment`,
and `log_path` and pass them in.
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
    """Outcome of `invoke_capture` — return code, captured stdout, cancel flag."""

    returncode: int
    stdout: str
    cancelled: bool


def hermes_arguments() -> list[str]:
    """Locate the Hermes binary and build the base argv (without prompt)."""
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
    """Translate a legacy `model.provider=custom` config into env vars.

    Mutates `environment` in place. Returns True when the legacy config was
    applied so the caller can also inject `--provider custom` into argv.
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
    """Strip `custom:*` entries from `auth.json`'s credential pool."""
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
    """Run Hermes with `prompt` appended as the last argv, log everything.

    Behavior-preserving counterpart of the old `invoke_hermes`. Returns the
    subprocess return code; stdout/stderr go straight to `log_path`. Used by
    the shard-execution pipeline.
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


# Only these env vars are mirrored into the capture log header — secrets like
# `CUSTOM_API_KEY` are deliberately omitted.
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
    """Run Hermes capturing stdout into memory AND mirroring it to `log_path`.

    Used by the Research Agent: it parses Hermes' JSON output and must be
    able to terminate the subprocess when its claim lease is lost (signalled
    via `cancel_event`). The log file contains the full command, an env
    summary, the stderr stream, and the captured stdout wrapped in
    `--- stdout ---` ... `--- end stdout ---` fences so a failure can be
    diagnosed without re-running Hermes.
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


def _terminate(process: "subprocess.Popen[str]") -> None:
    """Best-effort terminate: SIGTERM, wait 5s, then SIGKILL."""
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
