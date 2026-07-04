"""Host-side controlled build for workspace challenge artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.jsonio import read_json, write_json
from domain.pwn_artifact_evidence import PwnArtifactEvidenceError, refresh_pwn_debug_report
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
_MANAGED_IMAGE_LABEL = "ctf-factory.managed=true"
_WORKSPACE_LABEL_KEY = "ctf-factory.workspace_id"
_CHALLENGE_LABEL_KEY = "ctf-factory.challenge_id"
_CATEGORY_LABEL_KEY = "ctf-factory.category"


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
                    workspace_id=workspace.workspace_id,
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
        workspace_id: str,
        metadata: dict[str, Any],
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
        if metadata.get("category") == "pwn":
            _prepare_pwn_image(challenge_id, challenge_dir, workspace_id, metadata, compose)
        _reject_unsafe_build_files(challenge_id, dockerfile=dockerfile, compose=compose)
        category = str(metadata.get("category") or "")
        image = _metadata_image(challenge_id, metadata)
        command = _docker_build_command(
            image,
            workspace_id=workspace_id,
            challenge_id=challenge_id,
            category=category,
        )
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
        pwn_artifact_sha_changed = False
        if metadata.get("category") == "pwn":
            pwn_artifact_sha_changed = _sync_pwn_runtime_artifact(
                challenge_id,
                challenge_dir,
                image=image,
                metadata=metadata,
                timeout=min(30.0, float(self.timeout_seconds)),
            )
        prune_warning = _prune_workspace_dangling_images(
            workspace_id,
            timeout=min(60.0, float(self.timeout_seconds)),
        )
        _stamp_metadata(
            challenge_dir,
            command=command,
            image_id=image_id,
            log_path=_workspace_relative(log_path, challenge_dir),
            prune_warning=prune_warning,
            pwn_artifact_sha_changed=pwn_artifact_sha_changed,
        )
        if metadata.get("category") == "pwn":
            try:
                refresh_pwn_debug_report(challenge_dir)
            except PwnArtifactEvidenceError:
                pass
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
        results: list[HostBuildResult] = []
        for challenge_id, challenge_dir in validation_set.candidates.items():
            metadata = _read_metadata(challenge_dir)
            if metadata.get("category") in {"web", "pwn"}:
                if metadata.get("category") == "pwn":
                    compose = _safe_child(challenge_dir, "deploy/docker-compose.yml")
                    _prepare_pwn_image(challenge_id, challenge_dir, workspace.workspace_id, metadata, compose)
                image = _metadata_image(challenge_id, metadata)
                command = _docker_build_command(
                    image,
                    workspace_id=workspace.workspace_id,
                    challenge_id=challenge_id,
                    category=str(metadata.get("category") or ""),
                )
                _stamp_metadata(
                    challenge_dir,
                    command=command,
                    image_id="sha256:test",
                    log_path=None,
                    prune_warning=None,
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


def _docker_build_command(
    image: str,
    *,
    workspace_id: str,
    challenge_id: str,
    category: str,
) -> list[str]:
    command = ["docker", "build"]
    for label in _host_build_labels(
        workspace_id=workspace_id,
        challenge_id=challenge_id,
        category=category,
    ):
        command.extend(["--label", label])
    command.extend(["-t", image, "-f", "deploy/Dockerfile", "."])
    return command


def _host_build_labels(
    *,
    workspace_id: str,
    challenge_id: str,
    category: str,
) -> tuple[str, ...]:
    return (
        _MANAGED_IMAGE_LABEL,
        f"{_WORKSPACE_LABEL_KEY}={workspace_id}",
        f"{_CHALLENGE_LABEL_KEY}={challenge_id}",
        f"{_CATEGORY_LABEL_KEY}={category}",
    )


def _prune_workspace_dangling_images(workspace_id: str, *, timeout: float) -> str | None:
    command = [
        "docker",
        "image",
        "prune",
        "-f",
        "--filter",
        f"label={_MANAGED_IMAGE_LABEL}",
        "--filter",
        f"label={_WORKSPACE_LABEL_KEY}={workspace_id}",
    ]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return "docker CLI is not available; managed dangling image cleanup skipped"
    except subprocess.TimeoutExpired:
        return "managed dangling image cleanup timed out"
    except OSError as exc:
        return f"managed dangling image cleanup failed: {exc}"
    if result.returncode != 0:
        return (
            "managed dangling image cleanup failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return None


def _prepare_pwn_image(
    challenge_id: str,
    challenge_dir: Path,
    workspace_id: str,
    metadata: dict[str, Any],
    compose: Path,
) -> None:
    image = _pwn_workspace_image(workspace_id, challenge_dir.name or challenge_id)
    metadata["docker_image"] = image
    write_json(challenge_dir / "metadata.json", metadata)
    _rewrite_compose_image(compose, image)


def _sync_pwn_runtime_artifact(
    challenge_id: str,
    challenge_dir: Path,
    *,
    image: str,
    metadata: Mapping[str, Any],
    timeout: float,
) -> bool:
    artifact = metadata.get("artifact")
    if not isinstance(artifact, str) or not artifact.startswith("attachments/"):
        return False
    artifact_path = _safe_child(challenge_dir, artifact)
    runtime_path = _pwn_runtime_binary_path(challenge_dir, artifact_path.name)
    container_id = _docker_create_for_copy(challenge_id, image, timeout=timeout)
    try:
        _docker_cp_from_container(
            challenge_id,
            container_id,
            runtime_path,
            artifact_path,
            timeout=timeout,
        )
    finally:
        _docker_rm_container(container_id, timeout=min(10.0, timeout))
    return _stamp_pwn_artifact_metadata(challenge_dir, artifact_path)


def _pwn_runtime_binary_path(challenge_dir: Path, fallback_name: str) -> str:
    xinetd = challenge_dir / "deploy" / "_files" / "ctf.xinetd"
    try:
        text = xinetd.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"/home/ctf/{fallback_name}"
    match = re.search(r"(?m)^\s*server_args\s*=.*?/home/ctf\s+\./([^\s#]+)", text)
    if not match:
        return f"/home/ctf/{fallback_name}"
    return f"/home/ctf/{match.group(1).lstrip('/')}"


def _docker_create_for_copy(challenge_id: str, image: str, *, timeout: float) -> str:
    command = ["docker", "create", image]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HostBuildError(
            "docker CLI is not available on host",
            challenge_id=challenge_id,
            command=command,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HostBuildError(
            f"{challenge_id}: docker create timed out while syncing pwn artifact",
            challenge_id=challenge_id,
            command=command,
            stdout_tail=_tail_text(_decode_output(exc.stdout)),
            stderr_tail=_tail_text(_decode_output(exc.stderr)),
            failure_kind="artifact_sync",
            failure_hint="The host build could not create a container to copy the runtime ELF into attachments.",
        ) from exc
    if result.returncode != 0:
        raise HostBuildError(
            f"{challenge_id}: docker create failed while syncing pwn artifact",
            challenge_id=challenge_id,
            command=command,
            stdout_tail=_tail_text(result.stdout),
            stderr_tail=_tail_text(result.stderr),
            failure_kind="artifact_sync",
            failure_hint="The host build could not create a container to copy the runtime ELF into attachments.",
        )
    container_id = result.stdout.strip()
    if not container_id:
        raise HostBuildError(
            f"{challenge_id}: docker create returned no container id while syncing pwn artifact",
            challenge_id=challenge_id,
            command=command,
            failure_kind="artifact_sync",
        )
    return container_id


def _docker_cp_from_container(
    challenge_id: str,
    container_id: str,
    runtime_path: str,
    artifact_path: Path,
    *,
    timeout: float,
) -> None:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["docker", "cp", f"{container_id}:{runtime_path}", str(artifact_path)]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HostBuildError(
            f"{challenge_id}: docker cp timed out while syncing pwn artifact",
            challenge_id=challenge_id,
            command=command,
            stdout_tail=_tail_text(_decode_output(exc.stdout)),
            stderr_tail=_tail_text(_decode_output(exc.stderr)),
            failure_kind="artifact_sync",
            failure_hint="The runtime ELF could not be copied from the built image into attachments.",
        ) from exc
    if result.returncode != 0 or not artifact_path.is_file():
        raise HostBuildError(
            f"{challenge_id}: docker cp failed while syncing pwn artifact",
            challenge_id=challenge_id,
            command=command,
            stdout_tail=_tail_text(result.stdout),
            stderr_tail=_tail_text(result.stderr),
            failure_kind="artifact_sync",
            failure_hint=(
                f"Expected runtime ELF at {runtime_path}; align Dockerfile, "
                "xinetd server_args, and metadata.artifact."
            ),
        )


def _docker_rm_container(container_id: str, *, timeout: float) -> None:
    try:
        subprocess.run(
            ["docker", "rm", container_id],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return


def _stamp_pwn_artifact_metadata(challenge_dir: Path, artifact_path: Path) -> bool:
    metadata_path = challenge_dir / "metadata.json"
    metadata = read_json(metadata_path, {})
    if not isinstance(metadata, dict):
        raise HostBuildError(f"{challenge_dir.name}: metadata.json missing or invalid")
    old_sha = metadata.get("artifact_sha256")
    new_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    metadata["artifact_sha256"] = new_sha
    write_json(metadata_path, metadata)
    return isinstance(old_sha, str) and bool(old_sha) and old_sha != new_sha


def _pwn_workspace_image(workspace_id: str, challenge_name: str) -> str:
    workspace_prefix = _docker_component(workspace_id)[:6] or "local"
    slug = _pwn_challenge_slug(challenge_name)
    return f"pwn-{workspace_prefix}-{slug}:latest"


def _pwn_challenge_slug(challenge_name: str) -> str:
    safe_name = _docker_component(challenge_name)
    parts = [part for part in safe_name.split("-") if part]
    if parts and parts[0] == "pwn":
        parts = parts[1:]
    while len(parts) > 1 and _pwn_name_prefix_part(parts[0]):
        parts = parts[1:]
    return "-".join(parts) or safe_name or "challenge"


def _pwn_name_prefix_part(value: str) -> bool:
    return value.isdigit() or bool(re.fullmatch(r"[a-f0-9]{6,32}", value))


def _docker_component(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip(".-")


def _rewrite_compose_image(compose: Path, image: str) -> None:
    text = compose.read_text(encoding="utf-8", errors="replace")
    container_name = image.split(":", 1)[0]
    rewritten_lines: list[str] = []
    changed = False
    image_seen = False
    for line in text.splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        body = line[:-1] if newline else line
        if re.match(r"^\s*image\s*:", body):
            indent = body[: len(body) - len(body.lstrip())]
            replacement = f"{indent}image: {image}{newline}"
            rewritten_lines.append(replacement)
            changed = changed or replacement != line
            image_seen = True
            continue
        if re.match(r"^\s*container_name\s*:", body):
            indent = body[: len(body) - len(body.lstrip())]
            replacement = f"{indent}container_name: {container_name}{newline}"
            rewritten_lines.append(replacement)
            changed = changed or replacement != line
            continue
        rewritten_lines.append(line)
    if not image_seen:
        raise HostBuildError(f"{compose.parent.parent.name}: pwn compose file must declare a service image")
    if changed:
        compose.write_text("".join(rewritten_lines), encoding="utf-8")


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
    prune_warning: str | None = None,
    pwn_artifact_sha_changed: bool = False,
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
    if prune_warning:
        metadata["host_build_prune_warning"] = prune_warning
    else:
        metadata.pop("host_build_prune_warning", None)
    if pwn_artifact_sha_changed:
        metadata["solver_evidence_stale"] = True
        metadata["solver_evidence_stale_reason"] = (
            "host build synchronized a different runtime ELF into metadata.artifact; "
            "recompute pwn_debug_report.json and exploit offsets from attachments/"
        )
        for field in (
            "solve_status",
            "solve_note",
            "validation_status",
            "validation_failure_class",
            "validation_failure_signature",
            "validation_elapsed",
        ):
            metadata.pop(field, None)
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
