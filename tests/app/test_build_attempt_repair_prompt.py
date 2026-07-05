import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from domain.validation_repair_policy import ValidationRepairPolicy
from services.build_attempt_repair_service import (
    BuildAttemptRepairError,
    BuildAttemptRepairService,
    _assert_no_context_leak,
    _file_context,
    _repair_prompt,
)
from services.build_attempt_revalidation_service import BuildAttemptRevalidationError


def test_build_attempt_repair_prompt_anchors_terminal_to_allowed_root() -> None:
    challenge_dir = Path(
        "/workspace/executions/attempt/current/output/challenges/pwn/pwn-0001-demo"
    )

    prompt = _repair_prompt(
        {
            "id": "attempt",
            "design_task_id": "task",
            "challenge_id": "pwn-0001",
            "category": "pwn",
            "challenge_dir": challenge_dir,
            "failure_summary": "validate failed",
            "failure_details": [],
            "file_context": _file_context(challenge_dir),
        }
    )

    assert f"CHAL_ROOT={str(challenge_dir)!r}".replace("'", '"') in prompt
    assert 'cd "$CHAL_ROOT" || exit 1' in prompt
    assert "Do not call `./bin/progress`" in prompt
    assert "do not use relative guesses" in prompt
    assert "Never prepend `output/challenges/...`" in prompt
    assert "The same rule applies to file tools" in prompt
    assert "use `deploy/Dockerfile`, not" in prompt
    assert "If `pwd` prints `/`, immediately `cd \"$CHAL_ROOT\"`" in prompt
    assert "may contain the required literal `FLAG=<metadata.flag>`" in prompt
    assert "under `environment:` (singular)" in prompt
    assert "pwn-{workspace_id[:6]}-{challenge_slug}:latest" in prompt
    assert "do not invent or restore generic image names" in prompt
    assert "pwn-canary:latest" in prompt
    assert "prefer the workspace-scoped pattern" in prompt
    assert "ctf-factory.*" in prompt
    assert "Do not run broad `docker image prune`" in prompt
    assert "apt mirror" in prompt
    assert "Do not replace it with one hardcoded mirror" in prompt
    assert "Do not run any terminal command that contains `cd ./output/challenges/...`" in prompt
    normalized = " ".join(prompt.split())
    assert "Do not replace it with `${FLAG}`" in normalized
    assert "forbidden in player-facing `attachments/`" in normalized


def test_ai_repair_success_still_requires_revalidation(tmp_path: Path, monkeypatch) -> None:
    attempt_id = "11111111-1111-1111-1111-111111111111"
    current = tmp_path / "work" / "executions" / attempt_id / "current"
    challenge_dir = current / "output" / "challenges" / "pwn" / "pwn-0001-demo"
    challenge_dir.mkdir(parents=True)
    service = BuildAttemptRepairService.__new__(BuildAttemptRepairService)
    service.paths = type(
        "Paths",
        (),
        {"executions": tmp_path / "work" / "executions", "hermes_home": tmp_path / ".hermes"},
    )()
    service.progress = object()
    service.session_factory = object()
    service.timeout_seconds = 1
    service._prepare = lambda _attempt_id: {  # type: ignore[method-assign]
        "id": attempt_id,
        "design_task_id": "task",
        "challenge_id": "pwn-0001",
        "category": "pwn",
        "challenge_dir": str(challenge_dir),
        "failure_summary": "solver failed",
        "failure_details": [],
        "latest_failure": {
            "validation_status": "nonzero_exit",
            "validation_failure_class": "solver",
        },
        "repair_policy": ValidationRepairPolicy(
            failure_class="solver",
            route_type="hermes",
            deterministic_mechanics=(),
            hermes_allowed=True,
        ),
        "file_context": "",
    }
    service._revalidate = lambda _attempt_id: (_ for _ in ()).throw(  # type: ignore[method-assign]
        BuildAttemptRevalidationError("no flag")
    )
    monkeypatch.setattr("services.build_attempt_repair_service._hermes_arguments", lambda _category: ["hermes"])
    monkeypatch.setattr(
        "services.build_attempt_repair_service._hermes_environment",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr("services.build_attempt_repair_service.hermes_process.invoke", lambda *a, **k: 0)

    result = service.repair(attempt_id)  # type: ignore[arg-type]

    assert result.status == "failed"
    assert result.verification_status == "failed"
    assert "no flag" in (result.failure_summary or "")


def test_build_attempt_repair_prompt_includes_validation_evidence_and_debug_report(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "pwn-0001-demo"
    (challenge_dir / "writenup").mkdir(parents=True)
    (challenge_dir / "metadata.json").write_text('{"id":"pwn-0001","category":"pwn"}', encoding="utf-8")
    (challenge_dir / "validate.sh").write_text("#!/bin/sh\npython3 writenup/exp.py\n", encoding="utf-8")
    (challenge_dir / "writenup" / "exp.py").write_text("raise EOFError('Choice:')\n", encoding="utf-8")
    (challenge_dir / "writenup" / "pwn_debug_report.json").write_text(
        '{"failure_code":"pwn_prompt_eof","prompt_probe":"Choice:"}',
        encoding="utf-8",
    )

    prompt = _repair_prompt(
        {
            "id": "attempt",
            "design_task_id": "task",
            "challenge_id": "pwn-0001",
            "category": "pwn",
            "challenge_dir": challenge_dir,
            "failure_summary": "validate failed",
            "failure_details": [
                {"phase": "validate", "code": "pwn_prompt_eof", "message": "EOF"}
            ],
            "latest_failure": {
                "validation_status": "nonzero_exit",
                "validation_failure_class": "solver",
                "validation_failure_signature": "solver|code=pwn_prompt_eof|prompt=Choice:",
                "validation_stdout_tail": "banner",
                "validation_stderr_tail": "EOFError",
                "validation_returncode": 1,
                "validation_command": ["bash", "validate.sh"],
                "validation_diagnostic_unavailable": ["recent service logs unavailable"],
            },
            "file_context": _file_context(challenge_dir),
        }
    )

    assert "validation_failure_class: solver" in prompt
    assert "solver|code=pwn_prompt_eof" in prompt
    assert '"validation_stdout_tail": "banner"' in prompt
    assert '"validation_stderr_tail": "EOFError"' in prompt
    assert "Failure summary:\nvalidate failed" in prompt
    assert "--- validate.sh ---" in prompt
    assert "--- writenup/exp.py ---" in prompt
    assert "--- writenup/pwn_debug_report.json ---" in prompt
    assert "pwn_prompt_eof" in prompt
    normalized = " ".join(prompt.split())
    assert "Bound every `recvuntil` / `recvline` wait with short" in normalized
    assert "print bounded diagnostics for service ready state" in normalized


def test_build_attempt_repair_prompt_includes_final_pwn_artifact_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    challenge_dir = tmp_path / "pwn-0001-demo"
    (challenge_dir / "attachments").mkdir(parents=True)
    (challenge_dir / "deploy" / "src").mkdir(parents=True)
    (challenge_dir / "writenup").mkdir()
    artifact = b"\x7fELFfinal"
    deploy = b"\x7fELFdeploy"
    (challenge_dir / "attachments" / "vuln").write_bytes(artifact)
    (challenge_dir / "deploy" / "src" / "vuln").write_bytes(deploy)
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    deploy_sha = hashlib.sha256(deploy).hexdigest()
    (challenge_dir / "metadata.json").write_text(
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
                    "Symbol table '.symtab' contains 3 entries:\n"
                    "  1: 000000000040149d    42 FUNC    GLOBAL DEFAULT   15 win\n"
                    "  2: 0000000000401391    42 FUNC    GLOBAL DEFAULT   15 main\n"
                    "  3: 00000000004012ad    42 FUNC    GLOBAL DEFAULT   15 vuln\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr("domain.pwn_artifact_evidence.subprocess.run", fake_run)

    prompt = _repair_prompt(
        {
            "id": "attempt",
            "design_task_id": "task",
            "challenge_id": "pwn-0001",
            "category": "pwn",
            "challenge_dir": challenge_dir,
            "failure_summary": "stale evidence",
            "failure_details": [],
            "file_context": _file_context(challenge_dir),
        }
    )

    assert "FINAL SOLVER EVIDENCE SOURCE:" in prompt
    assert "Use only ./attachments/vuln for exp.py and pwn_debug_report.json." in prompt
    assert "Do not use deploy/src/vuln for solver offsets, symbols, gadgets, or report sha." in prompt
    assert "BINARY_SHA256 in exp.py is mandatory and must equal metadata.artifact_sha256." in prompt
    assert "pwn_debug_report.json is host-generated from attachments/vuln; do not hand-edit binary.sha256." in prompt
    assert f"attachments/vuln sha256: {artifact_sha}" in prompt
    assert f"metadata.artifact_sha256: {artifact_sha}" in prompt
    assert f"deploy/src/vuln sha256: {deploy_sha} (UNTRUSTED / DO NOT USE)" in prompt
    assert "- win: 0x40149d" in prompt
    assert "- main: 0x401391" in prompt
    assert "- vuln: 0x4012ad" in prompt


def test_file_context_omits_stale_pwn_debug_report_trusted_body(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "pwn-0001-demo"
    (challenge_dir / "writenup").mkdir(parents=True)
    (challenge_dir / "metadata.json").write_text(
        '{"id":"pwn-0001","category":"pwn","artifact_sha256":"current-sha"}',
        encoding="utf-8",
    )
    (challenge_dir / "writenup" / "pwn_debug_report.json").write_text(
        '{"binary":{"sha256":"old-sha"},"WIN_OFFSET":"0xdeadbeef"}',
        encoding="utf-8",
    )

    context = _file_context(challenge_dir)

    assert "--- writenup/pwn_debug_report.json ---" in context
    assert '"stale": true' in context
    assert "old-sha" in context
    assert "current-sha" in context
    assert "0xdeadbeef" not in context


def test_repair_context_rejects_other_attempt_execution_paths() -> None:
    current = "11111111-1111-1111-1111-111111111111"
    other = "22222222-2222-2222-2222-222222222222"

    with pytest.raises(BuildAttemptRepairError, match="orchestration-context-leak"):
        _assert_no_context_leak(
            current,
            {
                "id": current,
                "file_context": (
                    f"/root/ctf-skills/work/executions/{other}/current/output/"
                    "challenges/pwn/pwn-0001/writenup/exp.py"
                ),
            },
        )


def test_build_attempt_repair_prompt_marks_truncated_evidence(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "pwn-0001-demo"
    (challenge_dir / "writenup").mkdir(parents=True)
    (challenge_dir / "metadata.json").write_text('{"id":"pwn-0001","category":"pwn"}', encoding="utf-8")
    (challenge_dir / "validate.sh").write_text("#!/bin/sh\npython3 writenup/exp.py\n", encoding="utf-8")
    (challenge_dir / "writenup" / "exp.py").write_text("print('start')\n" + ("E" * 7000), encoding="utf-8")
    (challenge_dir / "writenup" / "pwn_debug_report.json").write_text("D" * 7000, encoding="utf-8")

    prompt = _repair_prompt(
        {
            "id": "attempt",
            "design_task_id": "task",
            "challenge_id": "pwn-0001",
            "category": "pwn",
            "challenge_dir": challenge_dir,
            "failure_summary": "validate failed",
            "failure_details": [],
            "latest_failure": {
                "validation_status": "nonzero_exit",
                "validation_failure_class": "solver",
                "validation_failure_signature": "solver|status=nonzero_exit",
                "validation_stdout_tail": "A" * 2500,
                "validation_stderr_tail": "B" * 2500,
            },
            "file_context": _file_context(challenge_dir),
        }
    )

    assert "<validation_stdout_tail truncated from 2500 chars to 2000>" in prompt
    assert "<validation_stderr_tail truncated from 2500 chars to 2000>" in prompt
    assert "<writenup/exp.py truncated from 7015 chars to 6000>" in prompt
    assert "<writenup/pwn_debug_report.json truncated from 7000 chars to 6000>" in prompt


def test_build_attempt_repair_prompt_sanitizes_nul_text_context(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "pwn-0001-demo"
    (challenge_dir / "writenup").mkdir(parents=True)
    (challenge_dir / "metadata.json").write_text('{"id":"pwn-0001","category":"pwn"}', encoding="utf-8")
    (challenge_dir / "validate.sh").write_text("printf 'ready\\x00done'\n", encoding="utf-8")
    (challenge_dir / "writenup" / "exp.py").write_text("BINARY_SHA256='abc'\x00\n", encoding="utf-8")
    (challenge_dir / "attachments").mkdir()
    (challenge_dir / "attachments" / "vuln").write_bytes(b"\x7fELF\x00binary")

    prompt = _repair_prompt(
        {
            "id": "attempt",
            "design_task_id": "task",
            "challenge_id": "pwn-0001",
            "category": "pwn",
            "challenge_dir": challenge_dir,
            "failure_summary": "stale\x00evidence",
            "failure_details": [],
            "latest_failure": {
                "validation_status": "nonzero_exit",
                "validation_failure_class": "solver",
                "validation_error": "solver stdout had NUL \x00 byte",
            },
            "file_context": _file_context(challenge_dir),
        }
    )

    assert "\x00" not in prompt
    assert r"stale\x00evidence" in prompt
    assert r"BINARY_SHA256='abc'\x00" in prompt
    assert "\x7fELF" not in prompt
