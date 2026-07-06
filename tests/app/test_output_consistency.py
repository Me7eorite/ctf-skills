from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from core.jsonio import read_json, write_json
from domain.output_consistency import output_manifest_hash, validate_workspace_success_state
from services.artifact_observation_governance import build_artifact_observation_payload


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


def test_build_artifact_observation_payload_marks_inconclusive_when_validation_unknown(tmp_path: Path) -> None:
    challenge = _candidate(tmp_path)
    metadata = read_json(challenge / "metadata.json")
    metadata["publishable"] = True
    metadata["solve_status"] = "unknown"
    metadata["validation_status"] = "unknown"
    write_json(challenge / "metadata.json", metadata)

    payload = build_artifact_observation_payload(
        challenge,
        build_attempt_id=uuid4(),
        design_evidence_id=None,
        contract_sha256=None,
    )

    assert payload["status"] == "inconclusive"
    assert payload["contract_checks"]["status_reason"] == "validation_fields_unknown"


def test_build_artifact_observation_payload_passes_bound_contract(tmp_path: Path) -> None:
    challenge = _candidate(tmp_path)
    (challenge / "deploy" / "src").mkdir(parents=True)
    (challenge / "deploy" / "src" / "app.py").write_text("import flask\n", encoding="utf-8")
    (challenge / "writenup").mkdir()
    (challenge / "writenup" / "exp.py").write_text("print('flag demo')\n", encoding="utf-8")
    metadata = read_json(challenge / "metadata.json")
    metadata.update(
        {
            "validation_final_flag_candidate": "flag{demo}",
            "language": "python",
            "runtime": "flask",
            "target_format": "container",
            "interaction": "http_form",
            "flag_concealment": "database_record",
            "contract_harness_results": {
                "direct-run": "passed",
                "recover-password-verification": "passed",
                "recover-password-dependency": "passed",
            },
        }
    )
    write_json(challenge / "metadata.json", metadata)
    profile = _governed_profile()

    payload = build_artifact_observation_payload(
        challenge,
        build_attempt_id=uuid4(),
        design_evidence_id=uuid4(),
        contract_sha256="contract-a",
        required_profile=profile,
        build_contract=_build_contract(profile),
    )

    assert payload["status"] == "passed"
    assert payload["contract_checks"]["profile_compare"] == "match"
    assert payload["observed_profile"]["language"] == "python"
    assert payload["fingerprints"]["source_token_sha256"]
    assert payload["fingerprints"]["solver_token_sha256"]
    assert payload["fingerprints"]["intended_path_sha256"]


def test_build_artifact_observation_payload_fails_profile_mismatch(tmp_path: Path) -> None:
    challenge = _candidate(tmp_path)
    metadata = read_json(challenge / "metadata.json")
    metadata.update(
        {
            "validation_final_flag_candidate": "flag{demo}",
            "language": "c",
            "runtime": "flask",
            "target_format": "container",
            "interaction": "http_form",
            "flag_concealment": "database_record",
            "contract_harness_results": {
                "direct-run": "passed",
                "recover-password-verification": "passed",
                "recover-password-dependency": "passed",
            },
        }
    )
    write_json(challenge / "metadata.json", metadata)
    profile = _governed_profile()

    payload = build_artifact_observation_payload(
        challenge,
        build_attempt_id=uuid4(),
        design_evidence_id=uuid4(),
        contract_sha256="contract-a",
        required_profile=profile,
        build_contract=_build_contract(profile),
    )

    assert payload["status"] == "failed"
    assert payload["contract_checks"]["status_reason"] == "implementation_contract_mismatch"


def test_build_artifact_observation_payload_fails_successful_shortcut(tmp_path: Path) -> None:
    challenge = _candidate(tmp_path)
    metadata = read_json(challenge / "metadata.json")
    metadata.update(_passing_governed_metadata())
    metadata["direct_run_reveals_flag"] = True
    metadata["contract_harness_results"].pop("direct-run")
    write_json(challenge / "metadata.json", metadata)
    profile = _governed_profile()

    payload = build_artifact_observation_payload(
        challenge,
        build_attempt_id=uuid4(),
        design_evidence_id=uuid4(),
        contract_sha256="contract-a",
        required_profile=profile,
        build_contract=_build_contract(profile),
    )

    assert payload["status"] == "failed"
    assert payload["contract_checks"]["status_reason"] == "unintended_solution_succeeded"


def test_build_artifact_observation_payload_detects_asset_flow_not_required(tmp_path: Path) -> None:
    challenge = _candidate(tmp_path)
    metadata = read_json(challenge / "metadata.json")
    metadata.update(_passing_governed_metadata())
    metadata["contract_harness_results"]["recover-password-verification"] = "failed"
    write_json(challenge / "metadata.json", metadata)
    profile = _governed_profile()

    payload = build_artifact_observation_payload(
        challenge,
        build_attempt_id=uuid4(),
        design_evidence_id=uuid4(),
        contract_sha256="contract-a",
        required_profile=profile,
        build_contract=_build_contract(profile),
    )

    assert payload["status"] == "failed"
    assert payload["contract_checks"]["status_reason"] == "asset_flow_not_required"


def test_build_artifact_observation_payload_inconclusive_without_harness_evidence(tmp_path: Path) -> None:
    challenge = _candidate(tmp_path)
    metadata = read_json(challenge / "metadata.json")
    metadata.update(_passing_governed_metadata())
    metadata.pop("contract_harness_results")
    write_json(challenge / "metadata.json", metadata)
    profile = _governed_profile()

    payload = build_artifact_observation_payload(
        challenge,
        build_attempt_id=uuid4(),
        design_evidence_id=uuid4(),
        contract_sha256="contract-a",
        required_profile=profile,
        build_contract=_build_contract(profile),
    )

    assert payload["status"] == "inconclusive"
    assert payload["contract_checks"]["status_reason"] == "observation_inconclusive"


def _governed_profile() -> dict[str, object]:
    return {
        "implementation": {
            "artifact_format": "container",
            "language": "python",
            "runtime": "flask",
            "interaction": "http_form",
            "flag_concealment": "database_record",
        }
    }


def _passing_governed_metadata() -> dict[str, object]:
    return {
        "validation_final_flag_candidate": "flag{demo}",
        "language": "python",
        "runtime": "flask",
        "target_format": "container",
        "interaction": "http_form",
        "flag_concealment": "database_record",
        "contract_harness_results": {
            "direct-run": "passed",
            "recover-password-verification": "passed",
            "recover-password-dependency": "passed",
        },
    }


def _build_contract(profile: dict[str, object]) -> dict[str, object]:
    return {
        "required_profile": profile,
        "required_player_actions": ["payload_injection"],
        "required_components": ["web-service"],
        "required_asset_flow": [
            {
                "stage_id": "recover-password",
                "produced_asset_or_capability": "admin password",
                "verification_harness": {
                    "id": "recover-password-verification",
                    "test_kind": "fixture_assertion",
                    "fixture_ref": "admin-password",
                    "assertion": "non_empty",
                },
                "dependency_harness": {
                    "id": "recover-password-dependency",
                    "test_kind": "solver_without_fixture",
                    "fixture_ref": "admin-password",
                    "assertion": "must_fail",
                },
            }
        ],
        "forbidden_shortcuts": [
            {
                "id": "direct-run",
                "test_kind": "artifact_direct_run",
                "artifact_ref": "primary",
                "assertion": "stdout_not_contains_flag",
            }
        ],
        "acceptance_tests": [],
        "allowed_implementation_freedom": [],
    }
