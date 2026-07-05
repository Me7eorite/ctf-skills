import hashlib
import json
import subprocess
from pathlib import Path

from domain.pwn_artifact_evidence import refresh_pwn_debug_report


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
