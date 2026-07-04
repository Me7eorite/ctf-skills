from pathlib import Path

from services.build_attempt_repair_service import _file_context, _repair_prompt


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
