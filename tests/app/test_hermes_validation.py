"""Unit coverage for Hermes validation event messages."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.resume import ChallengeResumePlan
from hermes.prompt import render_validation_repair_prompt
from hermes.runner import (
    _failed_challenge_debug_reports,
    _failed_pwn_final_artifact_evidence,
    _stamp_validation_results_into_outputs,
)
from hermes.validation import run_validation, validate_gate


@dataclass(frozen=True)
class _Paths:
    root: Path

    @property
    def challenges(self) -> Path:
        return self.root / "work" / "challenges"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(kwargs)
        return kwargs


class _ContractFailingValidator:
    def validate_challenge(self, challenge_id: str) -> dict[str, Any]:
        return {
            "challenge_id": challenge_id,
            "status": "contract_failed",
            "contract_errors": [
                "metadata.build_status is not passed",
                "missing deploy/Dockerfile",
            ],
        }


class _UnnecessaryPathValidator:
    def validate_challenge(self, challenge_id: str) -> dict[str, Any]:
        reason = (
            "flag is recoverable in plaintext without the intended technique "
            "(attachments/checker)"
        )
        return {
            "challenge_id": challenge_id,
            "status": "unnecessary_intended_path",
            "error": reason,
            "contract_errors": [reason],
        }


class _PathValidator:
    def __init__(self) -> None:
        self.seen: list[Path] = []

    def validate_path(
        self, challenge_dir: Path, *, expected_challenge_id: str
    ) -> dict[str, Any]:
        self.seen.append(challenge_dir)
        return {
            "challenge_id": expected_challenge_id,
            "status": "passed",
            "elapsed": 0.01,
            "returncode": 0,
            "command": ["bash", "validate.sh"],
            "stdout_tail": "flag{demo}\n",
            "final_flag_candidate": "flag{demo}",
        }


class _NoFlagPathValidator:
    def validate_path(
        self, challenge_dir: Path, *, expected_challenge_id: str
    ) -> dict[str, Any]:
        return {"challenge_id": expected_challenge_id, "status": "passed", "elapsed": 0.01}


def _make_gate_passing_web_challenge(paths: _Paths, challenge_id: str) -> Path:
    challenge = paths.challenges / "web" / f"{challenge_id}-demo"
    deploy = challenge / "deploy"
    (deploy / "src").mkdir(parents=True)
    (deploy / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (deploy / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    (deploy / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (challenge / "writenup").mkdir()
    (challenge / "writenup" / "exp.py").write_text("pass\n", encoding="utf-8")
    (challenge / "writenup" / "wp.md").write_text(
        "# wp\n\n## Build\n\n" + ("x" * 501) + "\n\n## Solve\n\n",
        encoding="utf-8",
    )
    (challenge / "README.md").write_text(
        "# readme\n\n## Build\n\n" + ("y" * 501) + "\n\n## Solve\n\n",
        encoding="utf-8",
    )
    (challenge / "validate.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(challenge / "validate.sh", 0o755)
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": challenge_id,
                "title": "Demo",
                "category": "web",
                "difficulty": "easy",
                "build_status": "passed",
                "solve_status": "passed",
                "build_command": "docker build -t demo:latest .",
                "docker_image": "demo:latest",
                "flag": "flag{demo}",
            }
        ),
        encoding="utf-8",
    )
    return challenge


def test_contract_errors_are_recorded_as_validation_error(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = _make_gate_passing_web_challenge(paths, "web-0001")
    recorder = _Recorder()

    results = run_validation(
        state=recorder,
        validator=_ContractFailingValidator(),  # type: ignore[arg-type]
        paths=paths,  # type: ignore[arg-type]
        image_exists=lambda _image: True,
        original_shard_name="web-0001-0001.json",
        worker="worker-01",
        challenge_ids=["web-0001"],
        plan_by_id={
            "web-0001": ChallengeResumePlan(
                challenge_id="web-0001",
                directory=challenge,
                lookup_status="ok",
                first_pending_stage="validate",
            )
        },
    )

    assert results[0]["validation_error"] == (
        "metadata.build_status is not passed; missing deploy/Dockerfile"
    )
    assert results[0]["validation_failure_details"][0]["code"] == "build_status_not_passed"
    failed_events = [
        event
        for event in recorder.events
        if event.get("stage") == "validate" and event.get("status") == "failed"
    ]
    message = failed_events[-1]["message"]
    assert message.startswith("validator: status=contract_failed ")
    assert "validation_failure_class=contract" in message
    assert "validation_failure_signature=contract|status=contract_failed" in message
    assert "error=metadata.build_status is not passed; missing deploy/Dockerfile" in message


def test_unnecessary_intended_path_reason_is_forwarded_to_repair(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = _make_gate_passing_web_challenge(paths, "web-0001")
    recorder = _Recorder()

    results = run_validation(
        state=recorder,
        validator=_UnnecessaryPathValidator(),  # type: ignore[arg-type]
        paths=paths,  # type: ignore[arg-type]
        image_exists=lambda _image: True,
        original_shard_name="web-0001-0001.json",
        worker="worker-01",
        challenge_ids=["web-0001"],
        plan_by_id={
            "web-0001": ChallengeResumePlan(
                challenge_id="web-0001",
                directory=challenge,
                lookup_status="ok",
                first_pending_stage="validate",
            )
        },
    )

    assert results[0]["validation_status"] == "unnecessary_intended_path"
    assert "attachments/checker" in results[0]["validation_error"]
    assert results[0]["validation_contract_errors"] == [
        "flag is recoverable in plaintext without the intended technique (attachments/checker)"
    ]
    assert results[0]["validation_failure_details"][0]["code"] == "plaintext_flag_exposure"
    failed_events = [
        event
        for event in recorder.events
        if event.get("stage") == "validate" and event.get("status") == "failed"
    ]
    assert "attachments/checker" in failed_events[-1]["message"]


def test_validation_repair_prompt_explains_unnecessary_intended_path() -> None:
    prompt = render_validation_repair_prompt(
        attempt=1,
        max_attempts=2,
        validation_results=[
            {
                "challenge_id": "re-0001",
                "solve_status": "failed",
                "validation_status": "unnecessary_intended_path",
                "validation_error": (
                    "flag is recoverable in plaintext without the intended "
                    "technique (attachments/checker)"
                ),
            }
        ],
    )

    assert '"unnecessary_intended_path"' in prompt
    assert "Focused debug plan:" in prompt
    assert "Root cause: the flag is reachable without the intended technique." in prompt
    assert "remove plaintext flag bytes" in prompt
    assert "`environment:` then `- FLAG=<metadata.flag>`" in prompt
    normalized = " ".join(prompt.split())
    assert "organizer deployment file may contain the plaintext flag" in normalized
    assert "binary print the flag when run with no input" in prompt


def test_validation_repair_prompt_classifies_nonzero_exit() -> None:
    prompt = render_validation_repair_prompt(
        attempt=1,
        max_attempts=2,
        validation_results=[
            {
                "challenge_id": "web-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "validation_returncode": 1,
                "validation_stderr_tail": "ModuleNotFoundError: No module named 'requests'",
            }
        ],
    )

    assert "Focused debug plan:" in prompt
    assert "Root cause: `validate.sh` exited non-zero." in prompt
    assert "Remove the missing dependency" in prompt
    assert "You may run `./validate.sh`" in prompt
    assert "Do not run `docker build`" in prompt
    assert "Pwn exploit debugging acceleration:" in prompt
    assert "context(os='linux', arch='amd64'" in prompt
    assert "ELF('./attachments/<binary>', checksec=False)" in prompt
    assert "BINARY_SHA256" in prompt
    assert "socket.create_connection" in prompt
    assert "process([binary_path])" in prompt
    assert "PWNLIB_LOG_LEVEL=debug" in prompt
    assert "remote(os.environ['CHAL_HOST']" in prompt
    assert "command -v gdb checksec readelf objdump" in prompt
    assert "gdb -q <binary>" in prompt
    assert "pwndbg/gef" in prompt


def test_validation_repair_prompt_describes_host_build_failure() -> None:
    prompt = render_validation_repair_prompt(
        attempt=1,
        max_attempts=2,
        validation_results=[
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "contract_failed",
                "validation_error": "host build failed: pwn-0001: docker build failed with exit 1",
                "failure_kind": "missing_dependency",
                "failure_hint": (
                    "The image is missing `make`; add it to the Dockerfile apt install "
                    "list before the build step."
                ),
                "failed_step": "Step 7: RUN make",
            }
        ],
    )

    assert "Root cause: the host Docker build failed before validation could start." in prompt
    assert "missing_dependency" in prompt
    assert "Step 7: RUN make" in prompt
    assert "keep `ubuntu:20.04`" in prompt


def test_validation_debug_prompt_guides_pwn_eof_debugging() -> None:
    prompt = render_validation_repair_prompt(
        attempt=1,
        max_attempts=2,
        validation_results=[
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "validation_stdout_tail": "pwnlib.tubes.sock.py raised EOFError",
            }
        ],
    )

    assert "For Pwn EOFs" in prompt
    assert "`ELF()` + `process()`" in prompt
    assert "remote(CHAL_HOST, CHAL_PORT)" in prompt


def test_validation_repair_prompt_blocks_metadata_replacement_cheat() -> None:
    prompt = render_validation_repair_prompt(
        attempt=2,
        max_attempts=2,
        validation_results=[
            {
                "challenge_id": "re-0001",
                "solve_status": "failed",
                "validation_status": "contract_failed",
                "validation_error": (
                    "re solver references 'metadata.json'; it must derive the "
                    "flag from the artifact, not organizer files"
                ),
                "validation_contract_errors": [
                    "validate.sh embeds the literal metadata.flag; the reference "
                    "solver must recover the flag, not hardcode it",
                    "re solver references 'metadata.json'; it must derive the flag "
                    "from the artifact, not organizer files",
                ],
            }
        ],
    )

    assert "Remove all `metadata.json` / `challenge.yml` reads" in prompt
    assert "do not compare against metadata inside the script" in prompt
    assert "same cheat in another form" in prompt


def test_validation_debug_prompt_includes_inherited_context() -> None:
    prompt = render_validation_repair_prompt(
        attempt=1,
        max_attempts=2,
        validation_results=[
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "validation_stderr_tail": "EOFError",
            }
        ],
        debug_context={
            "shard": {"category": "pwn", "topic": "canary"},
            "failed_challenge_files": {
                "pwn-0001": ["deploy/src/vuln.c", "writenup/exp.py"]
            },
        },
    )

    assert "Inherited build context:" in prompt
    assert '"topic": "canary"' in prompt
    assert "writenup/exp.py" in prompt


def test_validation_debug_context_includes_final_pwn_artifact_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    challenge = tmp_path / "challenges" / "pwn" / "pwn-0001-demo"
    (challenge / "attachments").mkdir(parents=True)
    (challenge / "deploy" / "src").mkdir(parents=True)
    artifact = b"\x7fELFfinal"
    deploy = b"\x7fELFdeploy"
    (challenge / "attachments" / "vuln").write_bytes(artifact)
    (challenge / "deploy" / "src" / "vuln").write_bytes(deploy)
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    deploy_sha = hashlib.sha256(deploy).hexdigest()
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

    def fake_run(command, **kwargs):
        if command[:2] == ["readelf", "-sW"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "Symbol table '.symtab' contains 2 entries:\n"
                    "  1: 000000000040149d    42 FUNC    GLOBAL DEFAULT   15 win\n"
                    "  2: 0000000000401391    42 FUNC    GLOBAL DEFAULT   15 main\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr("domain.pwn_artifact_evidence.subprocess.run", fake_run)

    evidence = _failed_pwn_final_artifact_evidence(
        tmp_path / "challenges",
        {"pwn-0001"},
    )

    assert evidence["pwn-0001"]["path"] == "./attachments/vuln"
    assert evidence["pwn-0001"]["sha256"] == artifact_sha
    assert evidence["pwn-0001"]["metadata_artifact_sha256"] == artifact_sha
    assert evidence["pwn-0001"]["deploy_src_vuln_sha256"] == deploy_sha
    assert evidence["pwn-0001"]["symbols"]["win"] == "0x40149d"
    assert "FINAL SOLVER EVIDENCE SOURCE" in evidence["pwn-0001"]["instruction"]
    assert "Use only ./attachments/vuln for exp.py and pwn_debug_report.json." in evidence["pwn-0001"]["instruction"]
    assert "Do not use deploy/src/vuln" in evidence["pwn-0001"]["instruction"]
    assert f"deploy/src/vuln sha256: {deploy_sha} (UNTRUSTED / DO NOT USE)" in evidence["pwn-0001"]["instruction"]

    prompt = render_validation_repair_prompt(
        attempt=1,
        max_attempts=1,
        validation_results=[
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "solver_evidence_stale",
            }
        ],
        debug_context={"pwn_final_artifact_evidence": evidence},
    )

    assert "FINAL SOLVER EVIDENCE SOURCE:" in prompt
    assert "BINARY_SHA256 in exp.py is mandatory and must equal metadata.artifact_sha256." in prompt
    assert f"deploy/src/vuln sha256: {deploy_sha} (UNTRUSTED / DO NOT USE)" in prompt


def test_pwn_debug_report_is_available_for_repair_context(tmp_path: Path) -> None:
    challenge = tmp_path / "challenges" / "pwn" / "pwn-0001-demo"
    (challenge / "writenup").mkdir(parents=True)
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "pwn-0001",
                "category": "pwn",
                "artifact_sha256": "current-sha",
            }
        ),
        encoding="utf-8",
    )
    (challenge / "writenup" / "pwn_debug_report.json").write_text(
        json.dumps(
            {
                "binary": {"sha256": "current-sha"},
                "failure_code": "pwn_bad_libc_base",
                "bases": {"libc": "0x7ffff7dc0000"},
            }
        ),
        encoding="utf-8",
    )

    reports = _failed_challenge_debug_reports(
        tmp_path / "challenges",
        {"pwn-0001"},
    )

    assert reports["pwn-0001"]["path"] == "writenup/pwn_debug_report.json"
    assert reports["pwn-0001"]["content"]["failure_code"] == "pwn_bad_libc_base"


def test_pwn_debug_report_is_marked_stale_when_sha_mismatches(tmp_path: Path) -> None:
    challenge = tmp_path / "challenges" / "pwn" / "pwn-0001-demo"
    (challenge / "writenup").mkdir(parents=True)
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "pwn-0001",
                "category": "pwn",
                "artifact_sha256": "current-sha",
            }
        ),
        encoding="utf-8",
    )
    (challenge / "writenup" / "pwn_debug_report.json").write_text(
        json.dumps(
            {
                "binary": {"sha256": "old-sha"},
                "failure_code": "pwn_bad_libc_base",
                "bases": {"libc": "0x7ffff7dc0000"},
            }
        ),
        encoding="utf-8",
    )

    reports = _failed_challenge_debug_reports(
        tmp_path / "challenges",
        {"pwn-0001"},
    )

    report = reports["pwn-0001"]
    assert report["stale"] is True
    assert "content" not in report
    assert report["metadata_artifact_sha256"] == "current-sha"
    assert report["report_binary_sha256"] == "old-sha"
    assert "readelf/objdump/checksec" in report["reason"]


def test_validate_gate_reports_specific_implementation_gap(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = paths.challenges / "re" / "re-0001-demo"
    challenge.mkdir(parents=True)
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": "re-0001",
                "category": "re",
                "build_status": "pending",
                "flag": "flag{demo}",
            }
        ),
        encoding="utf-8",
    )
    plan = ChallengeResumePlan(
        challenge_id="re-0001",
        directory=challenge,
        lookup_status="ok",
        first_pending_stage="validate",
    )

    error = validate_gate("re-0001", plan, paths, image_exists=lambda _image: True)  # type: ignore[arg-type]

    assert error == "implement evidence incomplete: src missing"


def _make_pwn_gate_challenge(paths: _Paths, challenge_id: str = "pwn-0001") -> Path:
    challenge = paths.challenges / "pwn" / f"{challenge_id}-demo"
    (challenge / "deploy" / "src").mkdir(parents=True)
    (challenge / "deploy" / "src" / "vuln.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    (challenge / "deploy" / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    (challenge / "deploy" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (challenge / "attachments").mkdir()
    artifact = b"\x7fELFfinal"
    (challenge / "attachments" / "vuln").write_bytes(artifact)
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    (challenge / "writenup").mkdir()
    (challenge / "writenup" / "wp.md").write_text("# wp\n\n## A\n\n" + "x" * 520 + "\n\n## B\n", encoding="utf-8")
    (challenge / "writenup" / "exp.py").write_text(f'BINARY_SHA256 = "{artifact_sha}"\n', encoding="utf-8")
    (challenge / "README.md").write_text("# readme\n\n## A\n\n" + "y" * 520 + "\n\n## B\n", encoding="utf-8")
    (challenge / "validate.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(challenge / "validate.sh", 0o755)
    (challenge / "metadata.json").write_text(
        json.dumps(
            {
                "id": challenge_id,
                "title": "Demo",
                "category": "pwn",
                "difficulty": "easy",
                "build_status": "passed",
                "build_command": "docker build -t pwn-demo:latest -f deploy/Dockerfile .",
                "docker_image": "pwn-demo:latest",
                "artifact": "attachments/vuln",
                "artifact_sha256": artifact_sha,
                "flag": "flag{demo}",
            }
        ),
        encoding="utf-8",
    )
    return challenge


def test_validate_gate_rejects_pwn_exp_missing_binary_sha_before_build(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = _make_pwn_gate_challenge(paths)
    (challenge / "writenup" / "exp.py").write_text("ARTIFACT_SHA256 = 'alias-only'\n", encoding="utf-8")
    plan = ChallengeResumePlan(
        challenge_id="pwn-0001",
        directory=challenge,
        lookup_status="ok",
        first_pending_stage="validate",
    )
    image_checks: list[str] = []

    error = validate_gate(
        "pwn-0001",
        plan,
        paths,  # type: ignore[arg-type]
        image_exists=lambda image: image_checks.append(image) or True,
    )

    assert isinstance(error, dict)
    assert error["status"] == "solver_evidence_stale"
    assert error["code"] == "pwn_exp_missing_binary_sha"
    assert "BINARY_SHA256" in error["message"]
    assert image_checks == []


def test_validate_gate_rejects_pwn_exp_binary_sha_mismatch(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = _make_pwn_gate_challenge(paths)
    (challenge / "writenup" / "exp.py").write_text('BINARY_SHA256 = "old-sha"\n', encoding="utf-8")
    plan = ChallengeResumePlan("pwn-0001", challenge, "ok", first_pending_stage="validate")

    error = validate_gate("pwn-0001", plan, paths, image_exists=lambda _image: True)  # type: ignore[arg-type]

    assert isinstance(error, dict)
    assert error["status"] == "solver_evidence_stale"
    assert error["code"] == "pwn_exp_binary_sha_mismatch"


def test_validate_gate_rejects_pwn_debug_report_sha_mismatch(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = _make_pwn_gate_challenge(paths)
    (challenge / "writenup" / "pwn_debug_report.json").write_text(
        json.dumps({"binary": {"path": "attachments/vuln", "sha256": "old-sha"}}),
        encoding="utf-8",
    )
    plan = ChallengeResumePlan("pwn-0001", challenge, "ok", first_pending_stage="validate")

    error = validate_gate("pwn-0001", plan, paths, image_exists=lambda _image: True)  # type: ignore[arg-type]

    assert isinstance(error, dict)
    assert error["status"] == "solver_evidence_stale"
    assert error["code"] == "pwn_debug_report_claims_wrong_artifact"


def test_validate_gate_rejects_pwn_exp_deploy_src_sha(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = _make_pwn_gate_challenge(paths)
    deploy = b"\x7fELFdeploy"
    (challenge / "deploy" / "src" / "vuln").write_bytes(deploy)
    deploy_sha = hashlib.sha256(deploy).hexdigest()
    (challenge / "writenup" / "exp.py").write_text(f'BINARY_SHA256 = "{deploy_sha}"\n', encoding="utf-8")
    plan = ChallengeResumePlan("pwn-0001", challenge, "ok", first_pending_stage="validate")

    error = validate_gate("pwn-0001", plan, paths, image_exists=lambda _image: True)  # type: ignore[arg-type]

    assert isinstance(error, dict)
    assert error["code"] == "pwn_evidence_from_deploy_src"
    assert "deploy/src/vuln" in error["message"]


def test_host_validation_overwrites_agent_claimed_passed_status(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = _make_pwn_gate_challenge(paths)
    metadata = json.loads((challenge / "metadata.json").read_text(encoding="utf-8"))
    metadata["solve_status"] = "passed"
    metadata["validation_status"] = "ready_for_validation"
    (challenge / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    changed = _stamp_validation_results_into_outputs(
        {"pwn-0001": challenge},
        [
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "validation_error": "solver failed after host validation",
            }
        ],
    )

    stamped = json.loads((challenge / "metadata.json").read_text(encoding="utf-8"))
    assert changed is True
    assert stamped["solve_status"] == "failed"
    assert stamped["validation_status"] == "nonzero_exit"
    assert stamped["solve_note"] == "solver failed after host validation"


def test_validate_gate_accepts_pwn_metadata_artifact_binary_name(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = _make_pwn_gate_challenge(paths)
    (challenge / "attachments" / "vuln").rename(challenge / "attachments" / "vault_service")
    artifact = (challenge / "attachments" / "vault_service").read_bytes()
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    (challenge / "writenup" / "exp.py").write_text(f'BINARY_SHA256 = "{artifact_sha}"\n', encoding="utf-8")
    metadata = json.loads((challenge / "metadata.json").read_text(encoding="utf-8"))
    metadata["artifact"] = "attachments/vault_service"
    metadata["artifact_sha256"] = artifact_sha
    (challenge / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    plan = ChallengeResumePlan("pwn-0001", challenge, "ok", first_pending_stage="validate")

    error = validate_gate("pwn-0001", plan, paths, image_exists=lambda _image: True)  # type: ignore[arg-type]

    assert error is None


def test_validate_gate_catches_missing_validate_and_wp_before_build(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    challenge = _make_pwn_gate_challenge(paths)
    (challenge / "validate.sh").unlink()
    (challenge / "writenup" / "wp.md").unlink()
    plan = ChallengeResumePlan("pwn-0001", challenge, "ok", first_pending_stage="validate")
    image_checks: list[str] = []

    error = validate_gate(
        "pwn-0001",
        plan,
        paths,  # type: ignore[arg-type]
        image_exists=lambda image: image_checks.append(image) or True,
    )

    assert isinstance(error, dict)
    assert error["status"] == "contract_failed"
    assert error["code"] == "missing_validation"
    assert error["path"] == "validate.sh"
    assert image_checks == []

    (challenge / "validate.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(challenge / "validate.sh", 0o755)
    error = validate_gate("pwn-0001", plan, paths, image_exists=lambda _image: True)  # type: ignore[arg-type]
    assert isinstance(error, dict)
    assert error["code"] == "missing_document"
    assert error["path"] == "writenup/wp.md"


def test_workspace_validation_uses_exact_bound_path(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    target = _make_gate_passing_web_challenge(
        _Paths(tmp_path / "execution" / "output"), "web-0001"
    )
    validator = _PathValidator()
    recorder = _Recorder()

    results = run_validation(
        state=recorder,
        validator=validator,  # type: ignore[arg-type]
        paths=paths,  # type: ignore[arg-type]
        image_exists=lambda _image: True,
        original_shard_name="web.iter-001.json",
        worker="worker-01",
        challenge_ids=["web-0001"],
        plan_by_id={},
        validation_targets={"web-0001": target},
    )

    assert results[0]["validation_status"] == "passed"
    assert validator.seen == [target]


def test_validator_passed_without_flag_candidate_is_not_solve_passed(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    target = _make_gate_passing_web_challenge(
        _Paths(tmp_path / "execution" / "output"), "web-0001"
    )
    recorder = _Recorder()

    results = run_validation(
        state=recorder,
        validator=_NoFlagPathValidator(),  # type: ignore[arg-type]
        paths=paths,  # type: ignore[arg-type]
        image_exists=lambda _image: True,
        original_shard_name="web.iter-001.json",
        worker="worker-01",
        challenge_ids=["web-0001"],
        plan_by_id={},
        validation_targets={"web-0001": target},
    )

    assert results[0]["solve_status"] == "failed"
    assert results[0]["validation_status"] == "pending_validation"
    assert "without a flag candidate" in results[0]["validation_error"]
