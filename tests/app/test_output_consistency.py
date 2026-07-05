from __future__ import annotations

from pathlib import Path

from core.jsonio import write_json
from domain.output_consistency import output_manifest_hash, validate_workspace_success_state


def _candidate(current: Path) -> Path:
    challenge = current / "output" / "challenges" / "web" / "web-0001-demo"
    challenge.mkdir(parents=True)
    write_json(
        challenge / "metadata.json",
        {
            "id": "web-0001",
            "category": "web",
            "solve_status": "passed",
            "validation_status": "passed",
            "publishable": True,
        },
    )
    (challenge / "validate.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (challenge / "logs").mkdir()
    write_json(
        challenge / "logs" / "report.json",
        {
            "challenges": [
                {
                    "id": "web-0001",
                    "solve_status": "passed",
                    "validation_status": "passed",
                }
            ]
        },
    )
    return challenge


def test_workspace_success_state_requires_existing_validated_output_path(tmp_path: Path) -> None:
    current = tmp_path / "current"
    state = current / "state"
    state.mkdir(parents=True)
    write_json(state / "publish-status.json", {"status": "succeeded", "output_manifest_hash": "abc"})
    write_json(
        state / "validated-output.json",
        {
            "output_manifest_hash": "abc",
            "output_paths": {"web-0001": "output/challenges/web/web-0001-demo"},
            "validate_paths": {"web-0001": "output/challenges/web/web-0001-demo/validate.sh"},
            "results": [
                {
                    "challenge_id": "web-0001",
                    "solve_status": "passed",
                    "validation_status": "passed",
                    "validation_returncode": 0,
                    "validation_final_flag_candidate": "flag{demo}",
                }
            ],
        },
    )

    result = validate_workspace_success_state(current)

    assert result["ok"] is False
    assert result["status"] == "validation_inconclusive"
    assert "validated output path is missing" in result["reason"]
    assert result["failure_details"][0]["code"] == "validated_output_path_missing"
    assert result["failure_details"][0]["path"] == "output/challenges/web/web-0001-demo"
    assert "repair_action" in result["failure_details"][0]


def test_workspace_success_state_accepts_matching_manifest_and_clean_artifacts(tmp_path: Path) -> None:
    current = tmp_path / "current"
    state = current / "state"
    state.mkdir(parents=True)
    challenge = _candidate(current)
    manifest_hash = output_manifest_hash({"web-0001": challenge})
    write_json(
        state / "publish-status.json",
        {"status": "succeeded", "output_manifest_hash": manifest_hash},
    )
    write_json(
        state / "validated-output.json",
        {
            "output_manifest_hash": manifest_hash,
            "output_paths": {"web-0001": "output/challenges/web/web-0001-demo"},
            "validate_paths": {"web-0001": "output/challenges/web/web-0001-demo/validate.sh"},
            "results": [
                {
                    "challenge_id": "web-0001",
                    "solve_status": "passed",
                    "validation_status": "passed",
                    "validation_returncode": 0,
                    "validation_final_flag_candidate": "flag{demo}",
                }
            ],
        },
    )

    result = validate_workspace_success_state(current)

    assert result["ok"] is True
    assert result["output_manifest_hash"] == manifest_hash


def test_workspace_success_state_reports_manifest_mismatch_as_repair_detail(tmp_path: Path) -> None:
    current = tmp_path / "current"
    state = current / "state"
    state.mkdir(parents=True)
    challenge = _candidate(current)
    manifest_hash = output_manifest_hash({"web-0001": challenge})
    (challenge / "README.md").write_text("changed after validation\n", encoding="utf-8")
    actual_hash = output_manifest_hash({"web-0001": challenge})
    write_json(
        state / "publish-status.json",
        {"status": "succeeded", "output_manifest_hash": manifest_hash},
    )
    write_json(
        state / "validated-output.json",
        {
            "output_manifest_hash": manifest_hash,
            "output_paths": {"web-0001": "output/challenges/web/web-0001-demo"},
            "validate_paths": {"web-0001": "output/challenges/web/web-0001-demo/validate.sh"},
            "results": [
                {
                    "challenge_id": "web-0001",
                    "solve_status": "passed",
                    "validation_status": "passed",
                    "validation_returncode": 0,
                    "validation_final_flag_candidate": "flag{demo}",
                }
            ],
        },
    )

    result = validate_workspace_success_state(current)

    assert result["status"] == "validation_inconclusive"
    detail = result["failure_details"][0]
    assert detail["code"] == "validated_manifest_hash_mismatch"
    assert detail["expected"] == manifest_hash
    assert detail["observed"] == actual_hash
