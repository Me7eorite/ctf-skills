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


def _workspace(tmp_path: Path, *, workspace_id: str = "exec") -> ExecutionWorkspace:
    root = tmp_path / workspace_id
    for name in ("output", "logs", "input", "state"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return ExecutionWorkspace(workspace_id, root)


def _expected_build_command(
    image: str,
    *,
    workspace_id: str = "exec",
    category: str,
    challenge_id: str,
) -> list[str]:
    return [
        "docker",
        "build",
        "--label",
        "ctf-factory.managed=true",
        "--label",
        f"ctf-factory.workspace_id={workspace_id}",
        "--label",
        f"ctf-factory.challenge_id={challenge_id}",
        "--label",
        f"ctf-factory.category={category}",
        "-t",
        image,
        "-f",
        "deploy/Dockerfile",
        ".",
    ]


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


def _pwn_challenge(workspace: ExecutionWorkspace, *, name: str = "pwn-0001-baby-stack") -> Path:
    challenge = workspace.output / "challenges" / "pwn" / name
    deploy = challenge / "deploy"
    (deploy / "src").mkdir(parents=True)
    (deploy / "src" / "vuln.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    (deploy / "Dockerfile").write_text(
        "FROM alpine\nCOPY deploy/src/vuln.c /tmp/vuln.c\n",
        encoding="utf-8",
    )
    (deploy / "docker-compose.yml").write_text(
        "services:\n"
        "  challenge:\n"
        "    image: pwn-demo:latest\n"
        "    container_name: pwn-demo\n"
        "    environment:\n"
        "      - FLAG=flag{demo}\n",
        encoding="utf-8",
    )
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "pwn-0001",
                "category": "pwn",
                "docker_image": "pwn-demo:latest",
                "build_status": "pending",
                "flag": "flag{demo}",
            }
        ),
        encoding="utf-8",
    )
    return challenge


def _pwn_challenge_with_artifact(workspace: ExecutionWorkspace) -> Path:
    challenge = _pwn_challenge(workspace)
    (challenge / "attachments").mkdir()
    (challenge / "attachments" / "vuln").write_bytes(b"host-elf")
    (challenge / "deploy" / "_files").mkdir(parents=True)
    (challenge / "deploy" / "_files" / "ctf.xinetd").write_text(
        "service ctf\n{\n"
        "  server = /usr/sbin/chroot\n"
        "  server_args = --userspec=1000:1000 /home/ctf ./vuln\n"
        "}\n",
        encoding="utf-8",
    )
    metadata = read_json(challenge / "metadata.json")
    metadata["artifact"] = "attachments/vuln"
    metadata["artifact_sha256"] = "old"
    (challenge / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
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
        if command[:3] == ["docker", "image", "prune"]:
            return subprocess.CompletedProcess(command, 0, stdout="Total reclaimed space: 0B\n", stderr="")
        assert kwargs["cwd"] == challenge
        return subprocess.CompletedProcess(command, 0, stdout="built\n", stderr="")

    with patch("hermes.host_build.subprocess.run", side_effect=fake_run):
        results = HostBuilder().build_workspace(workspace, validation_set)

    assert calls[0] == _expected_build_command(
        "web-0001-demo:latest",
        category="web",
        challenge_id="web-0001",
    )
    assert calls[2] == [
        "docker",
        "image",
        "prune",
        "-f",
        "--filter",
        "label=ctf-factory.managed=true",
        "--filter",
        "label=ctf-factory.workspace_id=exec",
    ]
    assert results[0].image == "web-0001-demo:latest"
    metadata = read_json(challenge / "metadata.json")
    assert metadata["build_status"] == "passed"
    assert metadata["host_build_command"] == calls[0]
    assert metadata["docker_image_id"] == "sha256:image"
    assert metadata["host_build_log"] == "logs/host-build/web-0001.docker-build.log"


def test_host_builder_rewrites_pwn_image_to_workspace_scoped_tag(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    challenge = _pwn_challenge(workspace)
    validation_set = WorkspaceValidationSet(
        candidates={"pwn-0001": challenge},
        output_manifest_hash="sha256:before",
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="sha256:image\n", stderr="")
        if command[:3] == ["docker", "image", "prune"]:
            return subprocess.CompletedProcess(command, 0, stdout="Total reclaimed space: 0B\n", stderr="")
        assert kwargs["cwd"] == challenge
        return subprocess.CompletedProcess(command, 0, stdout="built\n", stderr="")

    with patch("hermes.host_build.subprocess.run", side_effect=fake_run):
        results = HostBuilder().build_workspace(workspace, validation_set)

    expected_image = "pwn-exec-baby-stack:latest"
    assert calls[0] == _expected_build_command(
        expected_image,
        category="pwn",
        challenge_id="pwn-0001",
    )
    assert results[0].image == expected_image
    metadata = read_json(challenge / "metadata.json")
    compose = (challenge / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert metadata["docker_image"] == expected_image
    assert f"image: {expected_image}" in compose
    assert "container_name: pwn-exec-baby-stack" in compose


def test_host_builder_syncs_pwn_runtime_elf_to_attachment(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    challenge = _pwn_challenge_with_artifact(workspace)
    (challenge / "writenup").mkdir()
    (challenge / "writenup" / "pwn_debug_report.json").write_text(
        json.dumps({"binary": {"sha256": "old"}, "offset": 64}),
        encoding="utf-8",
    )
    metadata = read_json(challenge / "metadata.json")
    metadata.update(
        {
            "solve_status": "failed",
            "solve_note": "old offset failed",
            "validation_status": "nonzero_exit",
            "validation_failure_class": "service-readiness",
            "validation_failure_signature": "old",
            "validation_elapsed": 1.23,
        }
    )
    (challenge / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    validation_set = WorkspaceValidationSet(
        candidates={"pwn-0001": challenge},
        output_manifest_hash="sha256:before",
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="sha256:image\n", stderr="")
        if command[:2] == ["docker", "create"]:
            return subprocess.CompletedProcess(command, 0, stdout="container123\n", stderr="")
        if command[:2] == ["docker", "cp"]:
            Path(command[3]).write_bytes(b"runtime-elf")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["docker", "rm"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["docker", "image", "prune"]:
            return subprocess.CompletedProcess(command, 0, stdout="Total reclaimed space: 0B\n", stderr="")
        assert kwargs["cwd"] == challenge
        return subprocess.CompletedProcess(command, 0, stdout="built\n", stderr="")

    with patch("hermes.host_build.subprocess.run", side_effect=fake_run):
        HostBuilder().build_workspace(workspace, validation_set)

    expected_image = "pwn-exec-baby-stack:latest"
    assert ["docker", "create", expected_image] in calls
    assert ["docker", "cp", "container123:/home/ctf/vuln", str(challenge / "attachments" / "vuln")] in calls
    assert (challenge / "attachments" / "vuln").read_bytes() == b"runtime-elf"
    metadata = read_json(challenge / "metadata.json")
    assert metadata["artifact_sha256"] == "7b890f6cd0e6fa34864e726aa2cda390c35f43277e388d2e6b5c455dae01ba9b"
    assert metadata["solver_evidence_stale"] is True
    assert "attachments/" in metadata["solver_evidence_stale_reason"]
    for field in (
        "solve_status",
        "solve_note",
        "validation_status",
        "validation_failure_class",
        "validation_failure_signature",
        "validation_elapsed",
    ):
        assert field not in metadata


def test_host_builder_uses_short_pwn_slug_for_workspace_image(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, workspace_id="09c5542e-attempt")
    challenge = _pwn_challenge(workspace, name="pwn-09c5542e-0008-canary")
    validation_set = WorkspaceValidationSet(
        candidates={"pwn-0008": challenge},
        output_manifest_hash="sha256:before",
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="sha256:image\n", stderr="")
        if command[:3] == ["docker", "image", "prune"]:
            return subprocess.CompletedProcess(command, 0, stdout="Total reclaimed space: 0B\n", stderr="")
        assert kwargs["cwd"] == challenge
        return subprocess.CompletedProcess(command, 0, stdout="built\n", stderr="")

    with patch("hermes.host_build.subprocess.run", side_effect=fake_run):
        results = HostBuilder().build_workspace(workspace, validation_set)

    expected_image = "pwn-09c554-canary:latest"
    assert calls[0] == _expected_build_command(
        expected_image,
        workspace_id="09c5542e-attempt",
        category="pwn",
        challenge_id="pwn-0008",
    )
    assert calls[2] == [
        "docker",
        "image",
        "prune",
        "-f",
        "--filter",
        "label=ctf-factory.managed=true",
        "--filter",
        "label=ctf-factory.workspace_id=09c5542e-attempt",
    ]
    assert results[0].image == expected_image
    metadata = read_json(challenge / "metadata.json")
    compose = (challenge / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert metadata["docker_image"] == expected_image
    assert f"image: {expected_image}" in compose
    assert "container_name: pwn-09c554-canary" in compose


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
    assert error.value.command == _expected_build_command(
        "web-0001-demo:latest",
        category="web",
        challenge_id="web-0001",
    )
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


def test_host_builder_classifies_tuna_mirror_forbidden(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    challenge = _web_challenge(workspace)
    validation_set = WorkspaceValidationSet(
        candidates={"web-0001": challenge},
        output_manifest_hash="sha256:before",
    )

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            100,
            stdout=(
                "Step 4/10 : RUN apt-get update && apt-get install -y xinetd\n"
                "E: Failed to fetch http://mirrors.tuna.tsinghua.edu.cn/ubuntu/pool/main/x/xinetd "
                "403  Forbidden [IP: 101.6.15.130 80]\n"
            ),
            stderr="The command returned a non-zero code: 100\n",
        )

    with (
        patch("hermes.host_build.subprocess.run", side_effect=fake_run),
        pytest.raises(HostBuildError) as error,
    ):
        HostBuilder().build_workspace(workspace, validation_set)

    assert error.value.failure_kind == "apt_mirror_forbidden"
    assert "TUNA" in (error.value.failure_hint or "")
