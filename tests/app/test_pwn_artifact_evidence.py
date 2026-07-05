import hashlib
import json
import os
import subprocess
from pathlib import Path

from core.jsonio import read_json
from domain.pwn_artifact_evidence import ensure_pwn_solver_evidence, refresh_pwn_debug_report


def test_refresh_pwn_debug_report_reads_final_attachment_not_deploy_src(
    tmp_path: Path,
    monkeypatch,
) -> None:
    challenge = tmp_path / "pwn-0001-demo"
    (challenge / "attachments").mkdir(parents=True)
    (challenge / "deploy" / "src").mkdir(parents=True)
    (challenge / "writenup").mkdir()
    attachment = b"\x7fELFfinal"
    deploy = b"\x7fELFdeploy"
    (challenge / "attachments" / "vuln").write_bytes(attachment)
    (challenge / "deploy" / "src" / "vuln").write_bytes(deploy)
    attachment_sha = hashlib.sha256(attachment).hexdigest()
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "pwn-0001",
                "category": "pwn",
                "artifact": "attachments/vuln",
                "artifact_sha256": attachment_sha,
            }
        ),
        encoding="utf-8",
    )

    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        assert "deploy/src/vuln" not in str(command[-1])
        if command[:2] == ["readelf", "-sW"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "Symbol table '.symtab' contains 5 entries:\n"
                    "  1: 000000000040149d    42 FUNC    GLOBAL DEFAULT   15 win\n"
                    "  2: 0000000000401391    42 FUNC    GLOBAL DEFAULT   15 main\n"
                    "  3: 00000000004012ad    42 FUNC    GLOBAL DEFAULT   15 vuln\n"
                    "  4: 0000000000401250    42 FUNC    GLOBAL DEFAULT   15 setup_fake_stack\n"
                    "  5: 0000000000405000   256 OBJECT  GLOBAL DEFAULT   25 fake_stack\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr("domain.pwn_artifact_evidence.subprocess.run", fake_run)

    report_path = refresh_pwn_debug_report(challenge)

    assert report_path == challenge / "writenup" / "pwn_debug_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["binary"] == {
        "path": "attachments/vuln",
        "sha256": attachment_sha,
        "source": "final_artifact",
    }
    assert report["symbols"]["win"] == "0x40149d"
    assert report["symbols"]["main"] == "0x401391"
    assert report["symbols"]["vuln"] == "0x4012ad"
    assert report["symbols"]["setup_fake_stack"] == "0x401250"
    assert report["symbols"]["fake_stack"] == "0x405000"
    assert any(command[:2] == ["readelf", "-sW"] for command in commands)


def test_refresh_pwn_debug_report_uses_metadata_artifact_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    challenge = tmp_path / "pwn-0001-demo"
    (challenge / "attachments").mkdir(parents=True)
    (challenge / "writenup").mkdir()
    artifact = b"\x7fELFvault"
    (challenge / "attachments" / "vault_service").write_bytes(artifact)
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "pwn-0001",
                "category": "pwn",
                "artifact": "attachments/vault_service",
                "artifact_sha256": artifact_sha,
            }
        ),
        encoding="utf-8",
    )

    def fake_run(command, **kwargs):
        if command[:2] == ["readelf", "-sW"]:
            assert str(command[-1]).endswith("attachments/vault_service")
        if command[:2] == ["checksec", "--file"]:
            assert str(command[2]).endswith("attachments/vault_service")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr("domain.pwn_artifact_evidence.subprocess.run", fake_run)

    report_path = refresh_pwn_debug_report(challenge)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["binary"]["path"] == "attachments/vault_service"
    assert report["binary"]["sha256"] == artifact_sha


def test_ensure_pwn_solver_evidence_inserts_binary_sha_and_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    challenge = tmp_path / "pwn-0001-demo"
    (challenge / "attachments").mkdir(parents=True)
    (challenge / "writenup").mkdir()
    artifact = b"\x7fELFfinal"
    (challenge / "attachments" / "vuln").write_bytes(artifact)
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "pwn-0001",
                "category": "pwn",
                "artifact": "attachments/vuln",
                "artifact_sha256": "old-sha",
            }
        ),
        encoding="utf-8",
    )
    (challenge / "writenup" / "exp.py").write_text(
        "#!/usr/bin/env python3\nprint('flag{demo}')\n",
        encoding="utf-8",
    )

    def fake_run(command, **kwargs):
        if command[:2] == ["readelf", "-sW"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="  1: 000000000040149d    42 FUNC    GLOBAL DEFAULT   15 win\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr("domain.pwn_artifact_evidence.subprocess.run", fake_run)

    actions = ensure_pwn_solver_evidence(challenge)

    assert "updated metadata.artifact_sha256 from attachments/vuln" in actions
    assert read_json(challenge / "metadata.json")["artifact_sha256"] == artifact_sha
    exp_text = (challenge / "writenup" / "exp.py").read_text(encoding="utf-8")
    assert exp_text.startswith("#!/usr/bin/env python3\n")
    assert f'BINARY_SHA256 = "{artifact_sha}"' in exp_text
    report = read_json(challenge / "writenup" / "pwn_debug_report.json")
    assert report["binary"]["path"] == "attachments/vuln"
    assert report["binary"]["sha256"] == artifact_sha
    assert report["symbols"]["win"] == "0x40149d"


def test_ensure_pwn_solver_evidence_replaces_mismatched_binary_sha(tmp_path: Path) -> None:
    challenge = tmp_path / "pwn-0001-demo"
    (challenge / "attachments").mkdir(parents=True)
    (challenge / "writenup").mkdir()
    artifact = b"\x7fELFfinal"
    (challenge / "attachments" / "vuln").write_bytes(artifact)
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "pwn-0001",
                "category": "pwn",
                "artifact": "attachments/vuln",
                "artifact_sha256": artifact_sha,
            }
        ),
        encoding="utf-8",
    )
    (challenge / "writenup" / "exp.py").write_text(
        'BINARY_SHA256 = "deploy-or-old-sha"\n',
        encoding="utf-8",
    )

    ensure_pwn_solver_evidence(challenge)

    assert (challenge / "writenup" / "exp.py").read_text(encoding="utf-8") == (
        f'BINARY_SHA256 = "{artifact_sha}"\n'
    )


def test_ensure_pwn_solver_evidence_uses_metadata_artifact_name(tmp_path: Path) -> None:
    challenge = tmp_path / "pwn-0001-demo"
    (challenge / "attachments").mkdir(parents=True)
    (challenge / "deploy" / "src").mkdir(parents=True)
    (challenge / "writenup").mkdir()
    final = b"\x7fELFtaskqueue-final"
    deploy = b"\x7fELFtaskqueue-deploy"
    artifact_path = challenge / "attachments" / "taskqueue"
    artifact_path.write_bytes(final)
    (challenge / "deploy" / "src" / "taskqueue").write_bytes(deploy)
    artifact_sha = hashlib.sha256(final).hexdigest()
    deploy_sha = hashlib.sha256(deploy).hexdigest()
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "pwn-0001",
                "category": "pwn",
                "artifact": "attachments/taskqueue",
                "artifact_sha256": deploy_sha,
                "solver_evidence_stale": True,
                "solver_evidence_stale_reason": "runtime ELF changed",
                "validation_status": "solver_evidence_stale",
                "validation_failure_class": "old",
                "validation_failure_signature": "old",
                "solve_note": "old stale note",
            }
        ),
        encoding="utf-8",
    )
    (challenge / "writenup" / "exp.py").write_text(
        f'BINARY_SHA256 = "{deploy_sha}"\n',
        encoding="utf-8",
    )
    (challenge / "writenup" / "pwn_debug_report.json").write_text(
        json.dumps({"binary": {"path": "deploy/src/taskqueue", "sha256": deploy_sha}}),
        encoding="utf-8",
    )

    actions = ensure_pwn_solver_evidence(challenge)

    assert "updated metadata.artifact_sha256 from attachments/taskqueue" in actions
    metadata = read_json(challenge / "metadata.json")
    assert metadata["artifact_sha256"] == artifact_sha
    for field in (
        "solver_evidence_stale",
        "solver_evidence_stale_reason",
        "validation_status",
        "validation_failure_class",
        "validation_failure_signature",
        "solve_note",
    ):
        assert field not in metadata
    report = read_json(challenge / "writenup" / "pwn_debug_report.json")
    assert report["binary"] == {
        "path": "attachments/taskqueue",
        "sha256": artifact_sha,
        "source": "final_artifact",
    }
    exp_text = (challenge / "writenup" / "exp.py").read_text(encoding="utf-8")
    assert f'BINARY_SHA256 = "{artifact_sha}"' in exp_text


def test_ensure_pwn_solver_evidence_repairs_unreadable_attachment_mode(tmp_path: Path) -> None:
    challenge = tmp_path / "pwn-0001-demo"
    (challenge / "attachments").mkdir(parents=True)
    (challenge / "writenup").mkdir()
    artifact = b"\x7fELFfinal"
    artifact_path = challenge / "attachments" / "vuln"
    artifact_path.write_bytes(artifact)
    artifact_path.chmod(0o111)
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "pwn-0001",
                "category": "pwn",
                "artifact": "attachments/vuln",
                "artifact_sha256": artifact_sha,
            }
        ),
        encoding="utf-8",
    )
    (challenge / "writenup" / "exp.py").write_text("print('x')\n", encoding="utf-8")

    actions = ensure_pwn_solver_evidence(challenge)

    assert "made attachments/vuln readable for host validation" in actions
    assert artifact_path.stat().st_mode & 0o444 == 0o444
    assert os.access(artifact_path, os.R_OK)


def test_ensure_pwn_solver_evidence_is_isolated_across_multiple_pwn_challenges(
    tmp_path: Path,
) -> None:
    first_sha = _write_minimal_pwn_challenge(tmp_path / "pwn-0001-demo", "one-final", "one-deploy")
    second_sha = _write_minimal_pwn_challenge(tmp_path / "pwn-0002-demo", "two-final", "two-deploy")

    ensure_pwn_solver_evidence(tmp_path / "pwn-0001-demo")
    ensure_pwn_solver_evidence(tmp_path / "pwn-0002-demo")

    first_report = read_json(tmp_path / "pwn-0001-demo" / "writenup" / "pwn_debug_report.json")
    second_report = read_json(tmp_path / "pwn-0002-demo" / "writenup" / "pwn_debug_report.json")
    assert first_report["binary"]["sha256"] == first_sha
    assert second_report["binary"]["sha256"] == second_sha
    assert first_report["binary"]["sha256"] != second_report["binary"]["sha256"]
    assert f'BINARY_SHA256 = "{first_sha}"' in (
        tmp_path / "pwn-0001-demo" / "writenup" / "exp.py"
    ).read_text(encoding="utf-8")
    assert f'BINARY_SHA256 = "{second_sha}"' in (
        tmp_path / "pwn-0002-demo" / "writenup" / "exp.py"
    ).read_text(encoding="utf-8")


def _write_minimal_pwn_challenge(challenge: Path, final_text: str, deploy_text: str) -> str:
    (challenge / "attachments").mkdir(parents=True)
    (challenge / "deploy" / "src").mkdir(parents=True)
    (challenge / "writenup").mkdir()
    final = final_text.encode()
    (challenge / "attachments" / "vuln").write_bytes(final)
    (challenge / "deploy" / "src" / "vuln").write_bytes(deploy_text.encode())
    final_sha = hashlib.sha256(final).hexdigest()
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": challenge.name.split("-", 2)[0],
                "category": "pwn",
                "artifact": "attachments/vuln",
                "artifact_sha256": final_sha,
            }
        ),
        encoding="utf-8",
    )
    (challenge / "writenup" / "exp.py").write_text("print('x')\n", encoding="utf-8")
    return final_sha
