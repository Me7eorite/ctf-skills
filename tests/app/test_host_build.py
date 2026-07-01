from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core.jsonio import read_json
from hermes.build_publisher import WorkspaceValidationSet
from hermes.host_build import HostBuilder, HostBuildError
from hermes.workspace import ExecutionWorkspace


def _workspace(tmp_path: Path) -> ExecutionWorkspace:
    root = tmp_path / "exec"
    for name in ("output", "logs", "input", "state"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return ExecutionWorkspace("exec", root)


def _web_challenge(workspace: ExecutionWorkspace, *, compose: str | None = None) -> Path:
    challenge = workspace.output / "challenges" / "web" / "web-0001-demo"
    deploy = challenge / "deploy"
    (deploy / "src").mkdir(parents=True)
    (deploy / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (deploy / "Dockerfile").write_text(
        "FROM alpine\nCOPY deploy/_files/start.sh /root/start.sh\n",
        encoding="utf-8",
    )
    (deploy / "docker-compose.yml").write_text(
        compose
        or (
            "services:\n"
            "  web-0001-demo:\n"
            "    image: web-0001-demo:latest\n"
            "    environment:\n"
            "      - FLAG=flag{demo}\n"
        ),
        encoding="utf-8",
    )
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "web-0001",
                "category": "web",
                "docker_image": "web-0001-demo:latest",
                "build_status": "pending",
                "flag": "flag{demo}",
            }
        ),
        encoding="utf-8",
    )
    return challenge


def test_host_builder_runs_fixed_docker_build_and_stamps_metadata(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    challenge = _web_challenge(workspace)
    validation_set = WorkspaceValidationSet(
        candidates={"web-0001": challenge},
        output_manifest_hash="sha256:before",
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="sha256:image\n", stderr="")
        assert kwargs["cwd"] == challenge
        return subprocess.CompletedProcess(command, 0, stdout="built\n", stderr="")

    with patch("hermes.host_build.subprocess.run", side_effect=fake_run):
        results = HostBuilder().build_workspace(workspace, validation_set)

    assert calls[0] == [
        "docker",
        "build",
        "-t",
        "web-0001-demo:latest",
        "-f",
        "deploy/Dockerfile",
        ".",
    ]
    assert results[0].image == "web-0001-demo:latest"
    metadata = read_json(challenge / "metadata.json")
    assert metadata["build_status"] == "passed"
    assert metadata["build_command"] == "docker build -t web-0001-demo:latest -f deploy/Dockerfile ."
    assert metadata["docker_image_id"] == "sha256:image"
    assert metadata["host_build_log"] == "logs/host-build/web-0001.docker-build.log"


def test_host_builder_rejects_compose_volumes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    challenge = _web_challenge(
        workspace,
        compose=(
            "services:\n"
            "  web-0001-demo:\n"
            "    image: web-0001-demo:latest\n"
            "    volumes:\n"
            "      - ./data:/data\n"
        ),
    )
    validation_set = WorkspaceValidationSet(
        candidates={"web-0001": challenge},
        output_manifest_hash="sha256:before",
    )

    with pytest.raises(HostBuildError, match="forbidden runtime keys"):
        HostBuilder().build_workspace(workspace, validation_set)


def test_host_builder_failure_exposes_log_tails(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    challenge = _web_challenge(workspace)
    validation_set = WorkspaceValidationSet(
        candidates={"web-0001": challenge},
        output_manifest_hash="sha256:before",
    )

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="step 1\nCOPY failed\n",
            stderr="missing deploy/src/app.py\n",
        )

    with (
        patch("hermes.host_build.subprocess.run", side_effect=fake_run),
        pytest.raises(HostBuildError) as error,
    ):
        HostBuilder().build_workspace(workspace, validation_set)

    assert error.value.challenge_id == "web-0001"
    assert error.value.command == [
        "docker",
        "build",
        "-t",
        "web-0001-demo:latest",
        "-f",
        "deploy/Dockerfile",
        ".",
    ]
    assert "COPY failed" in (error.value.stdout_tail or "")
    assert "missing deploy/src/app.py" in (error.value.stderr_tail or "")


def test_host_builder_classifies_common_docker_failures(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    challenge = _web_challenge(workspace)
    validation_set = WorkspaceValidationSet(
        candidates={"web-0001": challenge},
        output_manifest_hash="sha256:before",
    )

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="Step 7/10 : RUN make\n",
            stderr="make: not found\n",
        )

    with (
        patch("hermes.host_build.subprocess.run", side_effect=fake_run),
        pytest.raises(HostBuildError) as error,
    ):
        HostBuilder().build_workspace(workspace, validation_set)

    assert error.value.failure_kind == "missing_dependency"
    assert error.value.failed_step == "Step 7: RUN make"
    assert "make" in (error.value.failure_hint or "")
