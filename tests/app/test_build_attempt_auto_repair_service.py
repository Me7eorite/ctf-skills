from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from core.jsonio import write_json
from core.paths import ProjectPaths
from services.build_attempt_auto_repair_service import auto_repair_challenge
from services.build_attempt_repair_service import (
    BuildAttemptRepairService,
    _challenge_directory,
)


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


def test_auto_repair_promotes_nested_challenge_when_root_metadata_missing(
    tmp_path: Path,
) -> None:
    challenge = tmp_path / "re-0001"
    nested = challenge / "output" / "challenges" / "re" / "re-0001-demo"
    nested.mkdir(parents=True)
    write_json(
        nested / "metadata.json",
        {
            "id": "re-0001",
            "category": "re",
            "artifact": "attachments/chal",
            "build_status": "passed",
        },
    )
    (nested / "validate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (nested / "attachments").mkdir()
    (nested / "attachments" / "chal").write_bytes(b"\x7fELF" + b"x" * 20)

    result = auto_repair_challenge(challenge, challenge_id="re-0001")

    assert result.changed
    assert (challenge / "metadata.json").is_file()
    assert (challenge / "validate.sh").is_file()
    assert (challenge / "attachments" / "chal").is_file()
    assert not (challenge / "output").exists()


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


def test_auto_repair_copies_re_deploy_source_to_src(tmp_path: Path) -> None:
    challenge = tmp_path / "re-0001-demo"
    deploy_source = challenge / "deploy" / "src" / "validator.c"
    deploy_source.parent.mkdir(parents=True)
    deploy_source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    write_json(
        challenge / "metadata.json",
        {
            "id": "re-0001",
            "category": "re",
            "artifact": "attachments/chal",
            "build_status": "passed",
        },
    )

    result = auto_repair_challenge(challenge)

    assert result.changed
    assert (challenge / "src" / "validator.c").read_text(encoding="utf-8") == (
        "int main(void) { return 0; }\n"
    )



def test_auto_repair_prefers_re_executable_artifact_over_data_pack(tmp_path: Path) -> None:
    challenge = tmp_path / "re-0001-demo"
    attachments = challenge / "attachments"
    attachments.mkdir(parents=True)
    data_pack = attachments / "game_assets.gres"
    data_pack.write_bytes(b"data")
    executable = attachments / "gres_viewer"
    executable.write_bytes(b"\x7fELF" + b"x" * 20)
    write_json(
        challenge / "metadata.json",
        {
            "id": "re-0001",
            "category": "re",
            "artifact": "attachments/game_assets.gres",
            "artifact_sha256": hashlib.sha256(data_pack.read_bytes()).hexdigest(),
            "build_status": "passed",
        },
    )

    result = auto_repair_challenge(challenge)

    metadata = json.loads((challenge / "metadata.json").read_text(encoding="utf-8"))
    assert result.changed
    assert metadata["artifact"] == "attachments/gres_viewer"
    assert metadata["artifact_sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()


def test_auto_repair_rewrites_re_validate_that_reads_metadata(tmp_path: Path) -> None:
    challenge = tmp_path / "re-0001-demo"
    (challenge / "attachments").mkdir(parents=True)
    (challenge / "attachments" / "chal").write_bytes(b"\x7fELF" + b"x" * 20)
    (challenge / "writenup").mkdir()
    (challenge / "writenup" / "exp.py").write_text("print('flag{demo}')\n", encoding="utf-8")
    (challenge / "validate.sh").write_text("#!/bin/sh\njq -r .flag metadata.json\n", encoding="utf-8")
    write_json(
        challenge / "metadata.json",
        {
            "id": "re-0001",
            "category": "re",
            "artifact": "attachments/chal",
            "build_status": "passed",
        },
    )

    result = auto_repair_challenge(challenge)

    validate_text = (challenge / "validate.sh").read_text(encoding="utf-8")
    assert result.changed
    assert "metadata.json" not in validate_text
    assert "writenup/exp.py ./attachments/chal" in validate_text

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
            "solve_status": "pending",
        },
    )
    (challenge / "challenge.yml").write_text("name: Demo\n", encoding="utf-8")
    (challenge / "README.md").write_text(
        "# Readme\n\n## Build\n" + ("A" * 350) + "\n\n## Solve\n",
        encoding="utf-8",
    )
    (challenge / "writenup").mkdir()
    (challenge / "writenup" / "wp.md").write_text(
        "# Writeup\n\n## Build\n" + ("A" * 350) + "\n\n## Solve\n",
        encoding="utf-8",
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


def test_repair_challenge_directory_prefers_execution_workspace_when_global_missing(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    attempt_id = UUID("00000000-0000-0000-0000-000000000001")
    challenge = (
        paths.executions
        / str(attempt_id)
        / "current"
        / "output"
        / "challenges"
        / "web"
        / "web-1234-demo"
    )
    write_json(challenge / "metadata.json", {"id": "web-1234", "category": "web"})

    found = _challenge_directory(paths, attempt_id, "web-1234", None)

    assert found == challenge


def test_repair_challenge_directory_normalizes_unclaimed_output_root(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    attempt_id = UUID("00000000-0000-0000-0000-000000000001")
    output_root = paths.executions / str(attempt_id) / "current" / "output"
    (output_root / "attachments").mkdir(parents=True)
    (output_root / "attachments" / "chal").write_bytes(b"\x7fELF" + b"x" * 20)
    (output_root / "validate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    write_json(
        output_root / "metadata.json",
        {"id": "re-0001", "category": "re", "build_status": "passed"},
    )

    found = _challenge_directory(paths, attempt_id, "re-0001", None, category="re")

    assert found == output_root / "challenges" / "re" / "re-0001"
    assert (found / "attachments" / "chal").is_file()
    assert (found / "validate.sh").is_file()
    assert (found / "metadata.json").is_file()
    assert not (output_root / "attachments").exists()
