"""Unit coverage for Hermes validation event messages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.resume import ChallengeResumePlan
from hermes.validation import run_validation


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
    failed_events = [
        event
        for event in recorder.events
        if event.get("stage") == "validate" and event.get("status") == "failed"
    ]
    assert failed_events[-1]["message"] == (
        "validator: status=contract_failed "
        "error=metadata.build_status is not passed; missing deploy/Dockerfile"
    )
