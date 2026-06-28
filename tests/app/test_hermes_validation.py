"""Unit coverage for Hermes validation event messages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.resume import ChallengeResumePlan
from hermes.prompt import render_validation_repair_prompt
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
    assert failed_events[-1]["message"] == (
        "validator: status=contract_failed "
        "error=metadata.build_status is not passed; missing deploy/Dockerfile"
    )


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
    assert "Focused repair plan:" in prompt
    assert "Root cause: the flag is reachable without the intended technique." in prompt
    assert "remove plaintext flag bytes" in prompt
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

    assert "Focused repair plan:" in prompt
    assert "Root cause: `validate.sh` exited non-zero." in prompt
    assert "Remove the missing dependency" in prompt


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
