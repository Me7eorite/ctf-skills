from __future__ import annotations

from pathlib import Path

from core.jsonio import write_json
from domain.validation_repair_policy import (
    MECHANIC_DOCUMENT_PAIR,
    MECHANIC_PWN_XINETD_SCAFFOLD,
    MECHANIC_VALIDATE_SOLVER_CAPTURE,
    policy_for_validation_failure,
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


def test_policy_does_not_auto_repair_timeout_but_manual_can_request_hermes() -> None:
    result = {"solve_status": "failed", "validation_status": "timeout"}

    automatic = policy_for_validation_failure(result)
    manual = policy_for_validation_failure(result, operator_triggered=True)

    assert automatic.route_type == "escalate"
    assert automatic.hermes_allowed is False
    assert manual.route_type == "hermes"
    assert manual.hermes_allowed is True


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
