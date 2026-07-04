from __future__ import annotations

from pathlib import Path

from core.jsonio import write_json
from domain.validation_repair_policy import (
    MECHANIC_DEPLOY_DOCKERFILE,
    MECHANIC_DOCUMENT_PAIR,
    MECHANIC_PWN_READINESS_PROBE,
    MECHANIC_PWN_XINETD_SCAFFOLD,
    MECHANIC_VALIDATE_SOLVER_CAPTURE,
    policy_for_validation_failure,
    validation_failure_fingerprints,
)
from services.build_attempt_auto_repair_service import auto_repair_challenge


def test_policy_routes_contract_to_mechanical_repairs() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "contract_failed",
        }
    )

    assert policy.route_type == "deterministic"
    assert policy.hermes_allowed is True
    assert MECHANIC_DOCUMENT_PAIR in policy.deterministic_mechanics


def test_policy_keeps_deferred_scaffold_and_dockerfile_out_of_contract_repairs() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "contract_failed",
        }
    )

    assert MECHANIC_PWN_XINETD_SCAFFOLD not in policy.deterministic_mechanics
    assert MECHANIC_DEPLOY_DOCKERFILE not in policy.deterministic_mechanics


def test_policy_keeps_deferred_scaffold_and_dockerfile_out_of_readiness_repairs() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "contract_failed",
            "validation_failure_details": [
                {"phase": "contract", "code": "pwn_bad_readiness_probe"}
            ],
        }
    )

    assert policy.failure_class == "service-readiness"
    assert MECHANIC_PWN_XINETD_SCAFFOLD not in policy.deterministic_mechanics
    assert MECHANIC_DEPLOY_DOCKERFILE not in policy.deterministic_mechanics


def test_policy_routes_solver_to_hermes_with_diagnostic_mechanics_only() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "validation_failure_details": [
                {"phase": "validate", "code": "missing_dependency"}
            ],
        }
    )

    assert policy.route_type == "hermes"
    assert policy.hermes_allowed is True
    assert MECHANIC_VALIDATE_SOLVER_CAPTURE in policy.deterministic_mechanics
    assert MECHANIC_DOCUMENT_PAIR not in policy.deterministic_mechanics
    assert MECHANIC_PWN_XINETD_SCAFFOLD not in policy.deterministic_mechanics


def test_policy_routes_generic_prompt_eof_without_readiness_as_solver() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "validation_failure_details": [
                {"phase": "validate", "code": "pwn_prompt_eof"}
            ],
            "validation_diagnostic_unavailable": [
                "readiness probe result unavailable"
            ],
        }
    )

    assert policy.failure_class == "solver"
    assert policy.route_type == "hermes"
    assert MECHANIC_VALIDATE_SOLVER_CAPTURE in policy.deterministic_mechanics
    assert MECHANIC_PWN_READINESS_PROBE not in policy.deterministic_mechanics


def test_policy_routes_prompt_eof_to_readiness_only_after_failed_fresh_observation() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "validation_failure_details": [
                {
                    "phase": "validate",
                    "code": "pwn_prompt_eof",
                    "readiness_observation": "failed-fresh-connection",
                }
            ],
        }
    )

    assert policy.failure_class == "service-readiness"
    assert policy.route_type == "deterministic"
    assert MECHANIC_PWN_READINESS_PROBE in policy.deterministic_mechanics


def test_validation_failure_fingerprints_keep_material_solver_and_readiness_failures_distinct() -> None:
    fingerprints = validation_failure_fingerprints(
        [
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "validation_stderr_tail": "ModuleNotFoundError: No module named 'pwn'",
            },
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "flag_mismatch",
            },
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "validation_failure_details": [
                    {
                        "phase": "validate",
                        "code": "pwn_prompt_eof",
                        "message": "recvuntil('Choice:') timed out",
                        "readiness_established": True,
                    }
                ],
            },
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "validation_failure_details": [
                    {
                        "phase": "validate",
                        "code": "pwn_prompt_eof",
                        "message": "recvuntil('Choice:') timed out",
                        "readiness_observation": "failed-fresh-connection",
                    }
                ],
            },
        ]
    )

    assert len(fingerprints) == 4
    assert len(set(fingerprints)) == 4
    assert any("solver:solver|status=nonzero_exit|missing_module=pwn" in item for item in fingerprints)
    assert any("solver:solver|status=flag_mismatch" in item for item in fingerprints)
    assert any("solver:solver|status=nonzero_exit|code=pwn_prompt_eof" in item for item in fingerprints)
    assert any(
        "service-readiness:service-readiness|status=nonzero_exit|code=pwn_prompt_eof" in item
        for item in fingerprints
    )


def test_policy_does_not_auto_repair_timeout_but_manual_can_request_hermes() -> None:
    result = {"solve_status": "failed", "validation_status": "timeout"}

    automatic = policy_for_validation_failure(result)
    manual = policy_for_validation_failure(result, operator_triggered=True)

    assert automatic.route_type == "escalate"
    assert automatic.hermes_allowed is False
    assert manual.route_type == "hermes"
    assert manual.hermes_allowed is True


def test_policy_routes_solver_io_timeout_to_bounded_solver_context() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "timeout",
            "validation_failure_details": [
                {
                    "phase": "validate",
                    "code": "solver_io_timeout",
                    "message": "recvuntil('Choice:') timed out",
                }
            ],
        }
    )

    assert policy.failure_class == "timeout"
    assert policy.route_type == "hermes"
    assert policy.hermes_allowed is True
    assert policy.max_deterministic_rounds == 1
    assert MECHANIC_VALIDATE_SOLVER_CAPTURE in policy.deterministic_mechanics
    assert MECHANIC_DOCUMENT_PAIR not in policy.deterministic_mechanics


def test_policy_routes_service_readiness_timeout_to_readiness_diagnostics() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "timeout",
            "validation_stderr_tail": "readiness probe timed out before banner",
        }
    )

    assert policy.failure_class == "timeout"
    assert policy.route_type == "deterministic"
    assert policy.hermes_allowed is False
    assert policy.max_deterministic_rounds == 1
    assert MECHANIC_PWN_READINESS_PROBE in policy.deterministic_mechanics
    assert MECHANIC_VALIDATE_SOLVER_CAPTURE in policy.deterministic_mechanics


def test_policy_routes_missing_diagnostics_timeout_to_diagnostic_capture() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "timeout",
            "validation_diagnostic_unavailable": ["missing diagnostic capture"],
        }
    )

    assert policy.failure_class == "timeout"
    assert policy.route_type == "deterministic"
    assert policy.hermes_allowed is False
    assert policy.max_deterministic_rounds == 1
    assert MECHANIC_VALIDATE_SOLVER_CAPTURE in policy.deterministic_mechanics
    assert MECHANIC_DOCUMENT_PAIR not in policy.deterministic_mechanics


def test_solver_diagnostic_mechanics_do_not_apply_contract_document_repairs(tmp_path: Path) -> None:
    challenge = tmp_path / "pwn-0001-demo"
    challenge.mkdir()
    write_json(
        challenge / "metadata.json",
        {"id": "pwn-0001", "category": "pwn"},
    )

    result = auto_repair_challenge(
        challenge,
        challenge_id="pwn-0001",
        allowed_mechanics=(MECHANIC_VALIDATE_SOLVER_CAPTURE,),
    )

    assert result.changed is False
    assert not (challenge / "README.md").exists()
