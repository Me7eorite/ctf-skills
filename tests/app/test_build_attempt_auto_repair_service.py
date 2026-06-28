from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from core.jsonio import write_json
from services.build_attempt_auto_repair_service import auto_repair_challenge
from services.build_attempt_repair_service import BuildAttemptRepairService


def test_auto_repair_removes_nested_output_and_copies_missing_writeup(tmp_path: Path) -> None:
    challenge = tmp_path / "re-0001-demo"
    nested = challenge / "src" / "output" / "challenges" / "re" / "re-0001-demo"
    nested.mkdir(parents=True)
    (nested / "metadata.json").write_text("{}", encoding="utf-8")
    write_json(
        challenge / "metadata.json",
        {
            "id": "re-0001",
            "category": "re",
            "artifact": "attachments/chal",
            "build_status": "passed",
        },
    )
    (challenge / "README.md").write_text(
        "# Readme\n\n## Build\n" + ("A" * 350) + "\n\n## Solve\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge)

    assert result.changed
    assert not (challenge / "src" / "output").exists()
    assert (challenge / "writenup" / "wp.md").is_file()


def test_auto_repair_copies_re_artifact_and_updates_metadata(tmp_path: Path) -> None:
    challenge = tmp_path / "re-0001-demo"
    source = challenge / "src" / "vmguard"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x7fELF" + b"x" * 20)
    write_json(
        challenge / "metadata.json",
        {
            "id": "re-0001",
            "category": "re",
            "artifact": "attachments/vmguard",
            "build_status": "pending",
        },
    )

    result = auto_repair_challenge(challenge)

    artifact = challenge / "attachments" / "vmguard"
    metadata = json.loads((challenge / "metadata.json").read_text(encoding="utf-8"))
    assert result.changed
    assert artifact.read_bytes() == source.read_bytes()
    assert metadata["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert metadata["build_status"] == "passed"
    assert metadata["target_format"] == "elf"
    assert metadata["build_command"] == "preserved existing artifact attachments/vmguard"


def test_auto_repair_is_noop_without_supported_mechanical_issue(tmp_path: Path) -> None:
    challenge = tmp_path / "re-0001-demo"
    (challenge / "attachments").mkdir(parents=True)
    artifact = challenge / "attachments" / "chal"
    artifact.write_bytes(b"\x7fELF" + b"x" * 20)
    write_json(
        challenge / "metadata.json",
        {
            "id": "re-0001",
            "category": "re",
            "artifact": "attachments/chal",
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "build_command": "gcc src/chal.c -o attachments/chal",
            "build_status": "passed",
            "target_format": "elf",
        },
    )

    result = auto_repair_challenge(challenge)

    assert not result.changed


def test_repair_service_skips_hermes_when_deterministic_repair_passes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    challenge = (
        tmp_path
        / "work"
        / "executions"
        / "attempt"
        / "current"
        / "output"
        / "challenges"
        / "re"
        / "re-0001-demo"
    )
    nested = challenge / "output" / "challenges" / "re" / "re-0001-demo"
    nested.mkdir(parents=True)
    (nested / "metadata.json").write_text("{}", encoding="utf-8")
    write_json(
        challenge / "metadata.json",
        {
            "id": "re-0001",
            "category": "re",
            "artifact": "attachments/chal",
            "build_status": "passed",
            "build_command": "gcc src/chal.c -o attachments/chal",
        },
    )
    (challenge / "attachments").mkdir()
    artifact = challenge / "attachments" / "chal"
    artifact.write_bytes(b"\x7fELF" + b"x" * 20)
    (challenge / "README.md").write_text(
        "# Readme\n\n## Build\n" + ("A" * 350) + "\n\n## Solve\n",
        encoding="utf-8",
    )

    service = BuildAttemptRepairService(paths=None, progress=object())
    service.paths = type(
        "Paths",
        (),
        {
            "executions": tmp_path / "work" / "executions",
            "root": tmp_path,
            "hermes_home": tmp_path / ".hermes",
        },
    )()
    attempt_id = uuid4()
    monkeypatch.setattr(
        service,
        "_prepare",
        lambda _attempt_id: {
            "id": attempt_id,
            "design_task_id": uuid4(),
            "challenge_id": "re-0001",
            "category": "re",
            "challenge_dir": str(challenge),
            "failure_summary": "nested generated output",
            "failure_details": [],
            "file_context": "",
        },
    )
    monkeypatch.setattr(service, "_revalidate", lambda _attempt_id: None)

    def fail_invoke(*_args, **_kwargs):
        raise AssertionError("Hermes should not run after deterministic repair passes")

    monkeypatch.setattr("services.build_attempt_repair_service.hermes_process.invoke", fail_invoke)

    result = service.repair(attempt_id)

    assert result.status == "succeeded"
    assert result.verification_status == "passed"
    assert result.failure_summary == "deterministic repair applied"
