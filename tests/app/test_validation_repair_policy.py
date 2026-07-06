from __future__ import annotations

from pathlib import Path

from core.jsonio import write_json
from domain.validation_repair_policy import (
    MECHANIC_ARTIFACT_HYGIENE,
    MECHANIC_DEPLOY_DOCKERFILE,
    MECHANIC_DOCUMENT_PAIR,
    MECHANIC_PWN_READINESS_PROBE,
    MECHANIC_PWN_SOLVER_EVIDENCE,
    MECHANIC_PWN_XINETD_SCAFFOLD,
    MECHANIC_VALIDATE_SOLVER_CAPTURE,
    no_progress_repair_blocked,
    policy_for_validation_failure,
    validation_failure_fingerprints,
    validation_repair_progress_fingerprints,
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
    assert MECHANIC_ARTIFACT_HYGIENE in policy.deterministic_mechanics


def test_policy_keeps_deferred_scaffold_and_dockerfile_out_of_contract_repairs() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "contract_failed",
        }
    )

    assert MECHANIC_PWN_XINETD_SCAFFOLD not in policy.deterministic_mechanics
    assert MECHANIC_DEPLOY_DOCKERFILE not in policy.deterministic_mechanics


def test_artifact_hygiene_repair_removes_pycache_and_pyc(tmp_path: Path) -> None:
    challenge = tmp_path / "pwn-0001-hygiene"
    cache = challenge / "writenup" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "exp.cpython-310.pyc").write_bytes(b"pyc")
    write_json(challenge / "metadata.json", {"id": "pwn-0001", "category": "pwn"})

    result = auto_repair_challenge(
        challenge,
        allowed_mechanics=(MECHANIC_ARTIFACT_HYGIENE,),
    )

    assert result.changed is True
    assert not cache.exists()
    assert not (cache / "exp.cpython-310.pyc").exists()
    assert any("writenup/__pycache__" in action for action in result.actions)


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

    assert policy.failure_class == "validate-wrapper"
    assert MECHANIC_PWN_XINETD_SCAFFOLD not in policy.deterministic_mechanics
    assert MECHANIC_DEPLOY_DOCKERFILE not in policy.deterministic_mechanics
    assert MECHANIC_PWN_READINESS_PROBE in policy.deterministic_mechanics


def test_policy_routes_validate_wrapper_conflict_to_probe_repair() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "validation_error": "service not ready",
            "validation_failure_details": [
                {"phase": "readiness", "code": "pwn_service_readiness_failed"}
            ],
            "pwn_debug_tcp_probe_status": "ready",
            "pwn_debug_tcp_probe_matched_token": "Choice:",
            "pwn_debug_tcp_probe_raw_output_tail": "Choice:",
        }
    )

    assert policy.failure_class == "validate-wrapper"
    assert policy.route_type == "deterministic"
    assert policy.hermes_allowed is True
    assert MECHANIC_PWN_READINESS_PROBE in policy.deterministic_mechanics


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


def test_policy_routes_pwn_solver_evidence_codes_to_solver_with_host_mechanic() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "contract_failed",
            "validation_failure_details": [
                {
                    "phase": "contract",
                    "code": "pwn_exp_missing_binary_sha",
                    "status": "solver_evidence_stale",
                }
            ],
        }
    )

    assert policy.failure_class == "solver"
    assert policy.route_type == "hermes"
    assert MECHANIC_PWN_SOLVER_EVIDENCE in policy.deterministic_mechanics
    assert MECHANIC_DOCUMENT_PAIR not in policy.deterministic_mechanics


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
    assert MECHANIC_PWN_SOLVER_EVIDENCE not in policy.deterministic_mechanics


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


def test_validation_failure_fingerprints_distinguish_stale_evidence_from_libc_leak() -> None:
    fingerprints = validation_failure_fingerprints(
        [
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "solver_evidence_stale",
                "metadata_artifact": "attachments/taskqueue",
                "artifact_sha256": "old-sha",
                "validation_failure_details": [
                    {"phase": "contract", "code": "solver_evidence_stale"}
                ],
            },
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "metadata_artifact": "attachments/taskqueue",
                "artifact_sha256": "fresh-sha",
                "pwn_failure_stage": "leak",
                "output_manifest_hash": "manifest-after-host-build",
                "validation_stdout_tail": "Service ready, running exploit\nFailed to leak libc base\n",
                "validation_failure_details": [
                    {"phase": "exploit", "code": "pwn_libc_leak_failed"}
                ],
            },
        ]
    )

    assert len(fingerprints) == 2
    assert any("solver_evidence_stale" in item and "old-sha" in item for item in fingerprints)
    assert any("pwn_libc_leak_failed" in item and "fresh-sha" in item for item in fingerprints)


def test_validation_repair_progress_fingerprints_track_diagnostic_improvement() -> None:
    before = validation_repair_progress_fingerprints(
        [
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "validation_failure_details": [{"code": "pwn_prompt_eof"}],
                "validation_diagnostic_unavailable": ["solver stdout unavailable"],
            }
        ]
    )
    after = validation_repair_progress_fingerprints(
        [
            {
                "challenge_id": "pwn-0001",
                "solve_status": "failed",
                "validation_status": "nonzero_exit",
                "validation_failure_details": [{"code": "pwn_prompt_eof"}],
                "solver_stdout_tail": "sent payload\\nBrokenPipeError",
                "pwn_debug_tcp_probe_status": "ready",
                "pwn_debug_tcp_probe_raw_output_tail": "Choice:",
            }
        ]
    )

    assert before != after
    assert "solver_stdout_visible" in after[0]
    assert "tcp_probe_raw_visible" in after[0]


def test_no_progress_repair_blocked_requires_unchanged_files_failure_and_diagnostics() -> None:
    before = [
        {
            "challenge_id": "pwn-0001",
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "validation_failure_details": [{"code": "pwn_prompt_eof"}],
        }
    ]
    same = [dict(before[0])]
    improved_diagnostics = [
        {
            **before[0],
            "solver_stdout_tail": "payload sent then BrokenPipeError",
        }
    ]

    assert no_progress_repair_blocked(
        before_file_fingerprint=("validate.sh=aaa", "writenup/exp.py=bbb"),
        after_file_fingerprint=("validate.sh=aaa", "writenup/exp.py=bbb"),
        before_results=before,
        after_results=same,
    )
    assert not no_progress_repair_blocked(
        before_file_fingerprint=("validate.sh=aaa", "writenup/exp.py=bbb"),
        after_file_fingerprint=("validate.sh=aaa", "writenup/exp.py=bbb"),
        before_results=before,
        after_results=improved_diagnostics,
    )
    assert not no_progress_repair_blocked(
        before_file_fingerprint=("validate.sh=aaa", "writenup/exp.py=bbb"),
        after_file_fingerprint=("validate.sh=ccc", "writenup/exp.py=bbb"),
        before_results=before,
        after_results=same,
    )


def test_policy_routes_pwn_libc_leak_failed_to_solver_not_readiness() -> None:
    policy = policy_for_validation_failure(
        {
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "pwn_failure_stage": "leak",
            "validation_failure_details": [
                {"phase": "exploit", "code": "pwn_libc_leak_failed"}
            ],
        }
    )

    assert policy.failure_class == "solver"
    assert policy.route_type == "hermes"


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
