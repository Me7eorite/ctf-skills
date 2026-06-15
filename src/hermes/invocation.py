"""Hermes subprocess invocation and compatibility helpers."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from core.paths import ProjectPaths

DEFAULT_HERMES_COMMAND = "hermes chat -Q --yolo -q"
DEFAULT_HERMES_TIMEOUT = 1500
HERMES_TIMEOUT_RETURNCODE = 124


def invoke_hermes(
    paths: ProjectPaths,
    prompt: str,
    log: Path,
    dry_run: bool,
    *,
    timeout: int | None = None,
    hermes_arguments: Callable[[], list[str]] | None = None,
    apply_legacy_custom_provider: Callable[[dict[str, str]], bool] | None = None,
    remove_conflicting_custom_pool: Callable[[], bool] | None = None,
) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        log.write_text(prompt + "\n", encoding="utf-8")
        return 0

    arguments = (hermes_arguments or default_hermes_arguments)()
    environment = os.environ.copy()
    if paths.hermes_home.exists() and not environment.get("HERMES_HOME"):
        environment["HERMES_HOME"] = str(paths.hermes_home)

    apply_provider = apply_legacy_custom_provider or (
        lambda env: apply_legacy_custom_provider_config(paths.hermes_home, env)
    )
    if apply_provider(environment):
        remove_pool = remove_conflicting_custom_pool or (
            lambda: remove_conflicting_custom_pool_config(paths.hermes_home)
        )
        remove_pool()
        query_index = arguments.index("-q") if "-q" in arguments else len(arguments)
        arguments[query_index:query_index] = ["--provider", "custom"]
    arguments.append(prompt)
    effective_timeout = timeout if timeout is not None else DEFAULT_HERMES_TIMEOUT

    with log.open("w", encoding="utf-8") as output:
        output.write(
            f"$ {' '.join(shlex.quote(arg) for arg in arguments[:-1])} <prompt>\n\n"
        )
        try:
            process = subprocess.run(
                arguments,
                cwd=paths.root,
                env=environment,
                text=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=effective_timeout,
                check=False,
            )
        except FileNotFoundError:
            output.write("Hermes command not found. Set HERMES_CMD or install Hermes.\n")
            return 127
        except subprocess.TimeoutExpired:
            output.write(f"\nHermes command timed out after {effective_timeout}s.\n")
            return HERMES_TIMEOUT_RETURNCODE
    return process.returncode


def apply_legacy_custom_provider_config(
    hermes_home: Path,
    environment: dict[str, str],
) -> bool:
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


def remove_conflicting_custom_pool_config(hermes_home: Path) -> bool:
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


def default_hermes_arguments() -> list[str]:
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
            [
                "--from",
                "hermes-agent",
                "hermes",
                "chat",
                "-Q",
                "--yolo",
                "-q",
            ]
        )
        return arguments
    return shlex.split(DEFAULT_HERMES_COMMAND)
