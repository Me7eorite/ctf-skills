"""Host-side controlled build for workspace challenge artifacts."""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.jsonio import read_json, write_json
from hermes.build_publisher import WorkspaceValidationSet
from hermes.workspace import ExecutionWorkspace

_DEFAULT_BUILD_TIMEOUT_SECONDS = 900
_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*(?::[A-Za-z0-9_.-]+)?$")
_DOCKER_STEP_RE = re.compile(r"Step (\d+)/(?:\d+) : (.+)")
_FORBIDDEN_DOCKERFILE_RE = re.compile(
    r"(?im)^\s*(?:RUN\s+)?(?:docker|docker-compose)\b"
)
_FORBIDDEN_COMPOSE_RE = re.compile(
    r"(?im)^\s*(?:volumes|privileged|network_mode|pid|ipc|devices)\s*:"
)


class HostBuildError(ValueError):
    """Raised when host-controlled build preparation or execution fails."""

    def __init__(
        self,
        message: str,
        *,
        challenge_id: str | None = None,
        command: list[str] | None = None,
        log_path: str | None = None,
        stdout_tail: str | None = None,
        stderr_tail: str | None = None,
        failure_kind: str | None = None,
        failure_hint: str | None = None,
        failed_step: str | None = None,
    ) -> None:
        super().__init__(message)
        self.challenge_id = challenge_id
        self.command = command
        self.log_path = log_path
        self.stdout_tail = stdout_tail
        self.stderr_tail = stderr_tail
        self.failure_kind = failure_kind
        self.failure_hint = failure_hint
        self.failed_step = failed_step


@dataclass(frozen=True)
class HostBuildResult:
    challenge_id: str
    image: str | None
    command: list[str] | None
    elapsed: float = 0.0
    image_id: str | None = None
    log_path: str | None = None
    skipped: bool = False


class HostBuilder:
    """Build web/pwn images on the host with a fixed Docker command shape."""

    def __init__(self, *, timeout_seconds: int = _DEFAULT_BUILD_TIMEOUT_SECONDS) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    def build_workspace(
        self,
        workspace: ExecutionWorkspace,
        validation_set: WorkspaceValidationSet,
    ) -> list[HostBuildResult]:
        results: list[HostBuildResult] = []
        log_dir = workspace.logs / "host-build"
        log_dir.mkdir(parents=True, exist_ok=True)
        for challenge_id, challenge_dir in validation_set.candidates.items():
            metadata = _read_metadata(challenge_dir)
            category = metadata.get("category")
            if category not in {"web", "pwn"}:
                results.append(
                    HostBuildResult(
                        challenge_id=challenge_id,
                        image=None,
                        command=None,
                        skipped=True,
                    )
                )
                continue
            results.append(
                self._build_challenge(
                    challenge_id,
                    challenge_dir,
                    metadata=metadata,
                    log_dir=log_dir,
                )
            )
        return results

    def _build_challenge(
        self,
        challenge_id: str,
        challenge_dir: Path,
        *,
        metadata: Mapping[str, Any],
        log_dir: Path,
    ) -> HostBuildResult:
        dockerfile = _safe_child(challenge_dir, "deploy/Dockerfile")
        compose = _safe_child(challenge_dir, "deploy/docker-compose.yml")
        if not dockerfile.is_file():
            raise HostBuildError(
                f"{challenge_id}: deploy/Dockerfile missing",
                challenge_id=challenge_id,
            )
        if not compose.is_file():
            raise HostBuildError(
                f"{challenge_id}: deploy/docker-compose.yml missing",
                challenge_id=challenge_id,
            )
        _reject_unsafe_build_files(challenge_id, dockerfile=dockerfile, compose=compose)
        image = _metadata_image(challenge_id, metadata)
        command = ["docker", "build", "-t", image, "-f", "deploy/Dockerfile", "."]
        log_path = log_dir / f"{challenge_id}.docker-build.log"
        started = time.monotonic()
        try:
            process = subprocess.run(
                command,
                cwd=challenge_dir,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise HostBuildError(
                "docker CLI is not available on host",
                challenge_id=challenge_id,
                command=command,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            _write_build_log(
                log_path,
                command=command,
                stdout=exc.stdout,
                stderr=exc.stderr,
                returncode=None,
                timed_out=True,
            )
            raise HostBuildError(
                f"{challenge_id}: docker build timed out",
                challenge_id=challenge_id,
                command=command,
                log_path=str(log_path),
                stdout_tail=_tail_text(_decode_output(exc.stdout)),
                stderr_tail=_tail_text(_decode_output(exc.stderr)),
                failure_kind="timeout",
                failure_hint="Docker build timed out; split the failing RUN step and shorten the expensive command.",
                failed_step=_infer_docker_step(_decode_output(exc.stdout), _decode_output(exc.stderr)),
            ) from exc
        elapsed = max(0.0, time.monotonic() - started)
        _write_build_log(
            log_path,
            command=command,
            stdout=process.stdout,
            stderr=process.stderr,
            returncode=process.returncode,
            timed_out=False,
        )
        if process.returncode != 0:
            stdout_text = _decode_output(process.stdout)
            stderr_text = _decode_output(process.stderr)
            failure_kind, failure_hint = _classify_docker_failure(
                stdout_text,
                stderr_text,
                returncode=process.returncode,
            )
            raise HostBuildError(
                f"{challenge_id}: docker build failed with exit {process.returncode}",
                challenge_id=challenge_id,
                command=command,
                log_path=str(log_path),
                stdout_tail=_tail_text(stdout_text),
                stderr_tail=_tail_text(stderr_text),
                failure_kind=failure_kind,
                failure_hint=failure_hint,
                failed_step=_infer_docker_step(stdout_text, stderr_text),
            )
        image_id = _inspect_image_id(image, timeout=min(10.0, float(self.timeout_seconds)))
        _stamp_metadata(
            challenge_dir,
            command=command,
            image_id=image_id,
            log_path=_workspace_relative(log_path, challenge_dir),
        )
        return HostBuildResult(
            challenge_id=challenge_id,
            image=image,
            command=command,
            elapsed=round(elapsed, 2),
            image_id=image_id,
            log_path=str(log_path),
        )


class NoopHostBuilder:
    """Test helper that keeps the host-build seam explicit without running Docker."""

    def build_workspace(
        self,
        workspace: ExecutionWorkspace,
        validation_set: WorkspaceValidationSet,
    ) -> list[HostBuildResult]:
        del workspace
        results: list[HostBuildResult] = []
        for challenge_id, challenge_dir in validation_set.candidates.items():
            metadata = _read_metadata(challenge_dir)
            if metadata.get("category") in {"web", "pwn"}:
                image = _metadata_image(challenge_id, metadata)
                command = ["docker", "build", "-t", image, "-f", "deploy/Dockerfile", "."]
                _stamp_metadata(
                    challenge_dir,
                    command=command,
                    image_id="sha256:test",
                    log_path=None,
                )
                results.append(
                    HostBuildResult(
                        challenge_id=challenge_id,
                        image=image,
                        command=command,
                    )
                )
            else:
                results.append(
                    HostBuildResult(
                        challenge_id=challenge_id,
                        image=None,
                        command=None,
                        skipped=True,
                    )
                )
        return results


def _read_metadata(challenge_dir: Path) -> dict[str, Any]:
    metadata = read_json(challenge_dir / "metadata.json", None)
    if not isinstance(metadata, dict):
        raise HostBuildError(f"{challenge_dir.name}: metadata.json missing or invalid")
    return metadata


def _metadata_image(challenge_id: str, metadata: Mapping[str, Any]) -> str:
    image = metadata.get("docker_image")
    if not isinstance(image, str) or not image.strip():
        raise HostBuildError(f"{challenge_id}: metadata.docker_image missing")
    image = image.strip()
    if not _IMAGE_RE.fullmatch(image):
        raise HostBuildError(f"{challenge_id}: metadata.docker_image is not an allowed tag")
    return image


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise HostBuildError(f"unsafe build path: {relative}") from exc
    return candidate


def _reject_unsafe_build_files(challenge_id: str, *, dockerfile: Path, compose: Path) -> None:
    dockerfile_text = dockerfile.read_text(encoding="utf-8", errors="replace")
    compose_text = compose.read_text(encoding="utf-8", errors="replace")
    if _FORBIDDEN_DOCKERFILE_RE.search(dockerfile_text):
        raise HostBuildError(
            f"{challenge_id}: deploy/Dockerfile invokes Docker",
            challenge_id=challenge_id,
        )
    if _FORBIDDEN_COMPOSE_RE.search(compose_text):
        raise HostBuildError(
            f"{challenge_id}: deploy/docker-compose.yml contains forbidden runtime keys",
            challenge_id=challenge_id,
        )


def _write_build_log(
    path: Path,
    *,
    command: list[str],
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    returncode: int | None,
    timed_out: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "command": command,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout": _decode_output(stdout),
        "stderr": _decode_output(stderr),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _tail_text(value: str, *, limit: int = 4000) -> str:
    return value[-limit:] if len(value) > limit else value


def _infer_docker_step(stdout: str, stderr: str) -> str | None:
    combined = f"{stdout}\n{stderr}"
    matches = list(_DOCKER_STEP_RE.finditer(combined))
    if not matches:
        return None
    step_no = matches[-1].group(1)
    command = matches[-1].group(2).strip()
    return f"Step {step_no}: {command}"


def _classify_docker_failure(
    stdout: str,
    stderr: str,
    *,
    returncode: int,
) -> tuple[str, str | None]:
    text = f"{stdout}\n{stderr}".lower()
    if returncode == 127 or "not found" in text:
        if "make: not found" in text or "make: command not found" in text:
            return (
                "missing_dependency",
                "The image is missing `make`; add it to the Dockerfile apt install list before the build step.",
            )
        if "gcc: not found" in text or "g++: not found" in text:
            return (
                "missing_dependency",
                "The image is missing the compiler toolchain; install the required compiler package in the Dockerfile.",
            )
        return (
            "missing_dependency",
            "A build command is missing from the image; install the required package or adjust the RUN step.",
        )
    if "cannot overwrite non-directory" in text:
        return (
            "filesystem_conflict",
            "The chroot copy layout collides with an existing path; avoid "
            "copying multiple library roots into the same destination without "
            "pre-creating the directory tree.",
        )
    if (
        "mirrors.tuna.tsinghua.edu.cn" in text
        and ("403  forbidden" in text or "failed to fetch" in text)
    ):
        return (
            "apt_mirror_forbidden",
            "The TUNA Ubuntu mirror returned 403; switch the Dockerfile apt "
            "source to an approved mirror such as 163, Aliyun, or USTC and "
            "retry the host build.",
        )
    if "copy failed" in text and "deploy/src" in text:
        return (
            "missing_source",
            "The build context or COPY path does not match the generated tree; "
            "verify the deploy/src files exist and the Dockerfile uses the "
            "right relative paths.",
        )
    if "permission denied" in text and ("mknod" in text or "chmod" in text):
        return (
            "permission_denied",
            "This step needs to run during Docker build as root; keep "
            "filesystem setup in the Dockerfile, not in start.sh.",
        )
    if "no such file or directory" in text:
        return (
            "missing_build_input",
            "A referenced file is missing from the build context; verify the Dockerfile COPY sources and filenames.",
        )
    return (
        "docker_exit_nonzero",
        "Docker exited non-zero; inspect the last failed step and the build "
        "log tails for the exact missing file or command.",
    )


def _inspect_image_id(image: str, *, timeout: float) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    image_id = result.stdout.strip()
    return image_id or None


def _stamp_metadata(
    challenge_dir: Path,
    *,
    command: list[str],
    image_id: str | None,
    log_path: str | None,
) -> None:
    metadata_path = challenge_dir / "metadata.json"
    metadata = read_json(metadata_path, {})
    if not isinstance(metadata, dict):
        raise HostBuildError(f"{challenge_dir.name}: metadata.json missing or invalid")
    metadata["build_status"] = "passed"
    metadata["build_command"] = " ".join(command)
    metadata["host_build_command"] = command
    if image_id:
        metadata["docker_image_id"] = image_id
    if log_path:
        metadata["host_build_log"] = log_path
    write_json(metadata_path, metadata)


def _workspace_relative(log_path: Path, challenge_dir: Path) -> str | None:
    active: Path | None = None
    for parent in challenge_dir.parents:
        if parent.name == "output":
            active = parent.parent
            break
    if active is None:
        return None
    try:
        return log_path.relative_to(active).as_posix()
    except ValueError:
        return None
