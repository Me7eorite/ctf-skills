from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from core.jsonio import write_json
from core.paths import ProjectPaths
from domain.validation_failure_governance import (
    annotate_validation_result,
    latest_failed_validation,
    normalized_validation_failure_class,
    timeout_failure_subreason,
    validation_failure_signature,
)


def test_readiness_detail_outranks_contract_status() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "contract_failed",
        "validation_failure_details": [
            {
                "phase": "contract",
                "code": "pwn_bad_readiness_probe",
                "message": "Pwn validate.sh uses CHAL_HOST/CHAL_PORT inside bash -c",
            }
        ],
    }

    assert normalized_validation_failure_class(result) == "validate-wrapper"


def test_nc_z_port_only_readiness_is_validate_wrapper() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "contract_failed",
        "validation_failure_details": [
            {
                "phase": "contract",
                "code": "pwn_port_only_readiness",
                "message": "Pwn validate.sh uses nc -z readiness",
            }
        ],
    }

    assert normalized_validation_failure_class(result) == "validate-wrapper"


def test_ready_broken_pipe_is_solver_prompt_desync() -> None:
    result = annotate_validation_result(
        {
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "readiness_established": True,
            "validation_stdout_tail": (
                "[validate] wait_app_ready read token Choice:\n"
                "[validate] run_solver\n"
            ),
            "validation_stderr_tail": "BrokenPipeError: [Errno 32] Broken pipe\n",
            "validation_failure_details": [
                {
                    "phase": "exploit",
                    "code": "pwn_solver_prompt_desync",
                    "message": "BrokenPipe after readiness",
                }
            ],
        }
    )

    assert result["validation_failure_class"] == "solver"
    assert result["pwn_failure_stage"] == "prompt_desync"


def test_canonical_probe_ready_reclassifies_validate_readiness_failure_as_wrapper() -> None:
    result = annotate_validation_result(
        {
            "challenge_id": "pwn-retwinwin",
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "validation_error": "validate.sh service readiness failed",
            "validation_stderr_tail": "service not ready: readiness probe failed",
            "validation_failure_details": [
                {
                    "phase": "readiness",
                    "code": "pwn_service_readiness_failed",
                    "message": "validate.sh service readiness failed",
                }
            ],
            "pwn_debug_tcp_probe_status": "ready",
            "pwn_debug_tcp_probe_matched_token": "Choice:",
            "pwn_debug_tcp_probe_raw_output_tail": "Welcome\nChoice:",
        }
    )

    assert result["validation_failure_class"] == "validate-wrapper"
    assert "validate-wrapper" in result["validation_failure_signature"]
    assert (
        "validate_says_service_not_ready_but_canonical_tcp_probe_ready"
        in result["classification_conflicts"]
    )
    assert "repair_validate_wrapper_before_service_startup" in result["classification_conflicts"]


def test_non_validation_phase_has_no_class() -> None:
    result = {"solve_status": "failed", "validation_status": "timeout"}

    assert normalized_validation_failure_class(result, runner_phase="hermes_timeout") is None


def test_validation_history_ignores_explicit_non_validation_round(tmp_path: Path) -> None:
    attempt_id = uuid4()
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    state_dir = paths.executions / str(attempt_id) / "current" / "state"
    state_dir.mkdir(parents=True)
    write_json(
        state_dir / "validation-history.json",
        [
            {
                "round": 1,
                "runner_phase": "hermes_timeout",
                "results": [
                    {
                        "challenge_id": "pwn-0001",
                        "solve_status": "failed",
                        "validation_status": "timeout",
                    }
                ],
            }
        ],
    )

    assert latest_failed_validation(paths, attempt_id) is None


def test_prompt_eof_without_readiness_evidence_prefers_solver() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "nonzero_exit",
        "validation_failure_details": [
            {
                "phase": "validate",
                "code": "pwn_prompt_eof",
                "message": "EOF waiting for Choice:",
            }
        ],
        "validation_diagnostic_unavailable": ["readiness probe result unavailable"],
    }

    assert normalized_validation_failure_class(result) == "solver"


def test_prompt_eof_is_readiness_only_when_fresh_probe_failed() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "nonzero_exit",
        "validation_failure_details": [
            {
                "phase": "validate",
                "code": "pwn_prompt_eof",
                "message": "EOF waiting for Choice:",
                "readiness_observation": "failed-fresh-connection",
            }
        ],
    }

    assert normalized_validation_failure_class(result) == "service-readiness"

    result["validation_failure_details"][0]["readiness_established"] = True

    assert normalized_validation_failure_class(result) == "solver"


def test_readiness_established_false_does_not_mean_fresh_probe_failed() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "nonzero_exit",
        "validation_failure_details": [
            {
                "phase": "validate",
                "code": "pwn_prompt_eof",
                "message": "EOF waiting for Choice:",
                "readiness_established": False,
            }
        ],
    }

    assert normalized_validation_failure_class(result) == "solver"


def test_exploit_phase_nonzero_outranks_readiness_code() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "nonzero_exit",
        "validation_stdout_tail": "Service is ready\nRunning exploit...\n",
        "validation_stderr_tail": "readiness probe passed earlier; exp.py exited 1",
        "validation_failure_details": [
            {
                "phase": "exploit",
                "code": "pwn_service_readiness_failed",
                "message": "legacy readiness code should not override exploit phase",
            }
        ],
    }

    assert normalized_validation_failure_class(result) == "solver"


def test_libc_leak_failure_overrides_readiness_stage_and_route() -> None:
    result = annotate_validation_result(
        {
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "pwn_failure_stage": "readiness",
            "validation_stdout_tail": (
                "Service ready, running exploit...\n"
                "[*] === Stage 1: Libc leak via unsorted bin ===\n"
                "Leak data ... 000000...\n"
                "[-] Failed to leak libc base\n"
            ),
            "validation_stderr_tail": "[readiness] no banner or menu prompt received\n",
            "validation_failure_details": [
                {
                    "phase": "exploit",
                    "code": "pwn_libc_leak_failed",
                    "message": "Failed to leak libc base",
                }
            ],
        }
    )

    assert result["validation_failure_class"] == "solver"
    assert result["pwn_failure_stage"] == "leak"
    assert "pwn_libc_leak_failed" in result["validation_failure_signature"]


def test_exploit_phase_timeout_is_solver_io_timeout_not_readiness() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "timeout",
        "validation_stdout_tail": "Service is ready\nRunning exploit...\n",
        "validation_failure_details": [
            {
                "phase": "exploit",
                "code": "exploit_timeout",
                "message": "recvuntil('Choice:') timed out after exploit start",
            }
        ],
    }

    assert normalized_validation_failure_class(result) == "timeout"
    assert timeout_failure_subreason(result) == "solver_io"


def test_progress_message_fallback_requires_validation_phase() -> None:
    paths = ProjectPaths(root=Path("/tmp/unused"), repository=Path("/tmp/unused"))

    assert (
        latest_failed_validation(
            paths,
            uuid4(),
            progress_messages=["phase=hermes_timeout status=timeout error=timed out"],
        )
        is None
    )

    summary = latest_failed_validation(
        paths,
        uuid4(),
        progress_messages=["phase=validation status=timeout error=validate.sh timed out"],
    )

    assert summary is not None
    assert summary["validation_failure_class"] == "timeout"


def test_timeout_signature_preserves_solver_io_subreason() -> None:
    result = {
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

    assert normalized_validation_failure_class(result) == "timeout"
    assert timeout_failure_subreason(result) == "solver_io"

    signature = validation_failure_signature(result)

    assert signature is not None
    assert signature.startswith("timeout|status=timeout")
    assert "timeout_subreason=solver_io" in signature
    assert "code=solver_io_timeout" in signature


def test_timeout_subreason_detects_service_readiness_from_text() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "timeout",
        "validation_stderr_tail": "readiness probe timed out before banner",
    }

    assert normalized_validation_failure_class(result) == "timeout"
    assert timeout_failure_subreason(result) == "service_readiness"
    assert "timeout_subreason=service_readiness" in (validation_failure_signature(result) or "")


def test_timeout_subreason_marks_missing_diagnostics() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "timeout",
        "validation_diagnostic_unavailable": ["missing diagnostic capture"],
    }

    assert normalized_validation_failure_class(result) == "timeout"
    assert timeout_failure_subreason(result) == "missing_diagnostics"
    assert "timeout_subreason=missing_diagnostics" in (validation_failure_signature(result) or "")


def test_solver_dependency_signature_keeps_missing_module_and_normalizes_noise() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "nonzero_exit",
        "validation_stderr_tail": (
            "Traceback\n"
            '  File "/workspace/executions/123/current/output/challenges/pwn/x/writenup/exp.py", line 7, in <module>\n'
            "ModuleNotFoundError: No module named 'pwn'\n"
            "elapsed=13.52s container=abcdef1234567890 port=31337"
        ),
    }

    signature = validation_failure_signature(result)

    assert signature is not None
    assert signature.startswith("solver|status=nonzero_exit")
    assert "missing_module=pwn" in signature
    assert "/workspace/executions/123" not in signature
    assert "31337" not in signature
    assert "abcdef1234567890" not in signature


def test_latest_failed_validation_prefers_history_over_report(tmp_path: Path) -> None:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    attempt_id = uuid4()
    state = paths.executions / str(attempt_id) / "current" / "state"
    state.mkdir(parents=True)
    report = paths.executions / str(attempt_id) / "current" / "logs" / "report.json"
    report.parent.mkdir(parents=True)
    write_json(
        report,
        {
            "challenges": [
                {
                    "id": "pwn-0001",
                    "solve_status": "failed",
                    "validation_status": "flag_mismatch",
                }
            ]
        },
    )
    write_json(
        state / "validation-history.json",
        [
            {
                "round": 0,
                "results": [
                    {
                        "challenge_id": "pwn-0001",
                        "solve_status": "failed",
                        "validation_status": "contract_failed",
                        "validation_failure_details": [
                            {
                                "phase": "contract",
                                "code": "missing_metadata_field",
                                "message": "metadata.flag missing",
                                "path": "metadata.json",
                            }
                        ],
                    }
                ],
            }
        ],
    )

    summary = latest_failed_validation(paths, attempt_id)

    assert summary is not None
    assert summary["source"] == "validation-history"
    assert summary["round"] == 0
    assert summary["validation_failure_class"] == "contract"
    assert "missing_metadata_field" in summary["validation_failure_signature"]


def test_latest_failed_validation_uses_report_when_history_is_missing(tmp_path: Path) -> None:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    attempt_id = uuid4()
    report = paths.executions / str(attempt_id) / "current" / "logs" / "report.json"
    report.parent.mkdir(parents=True)
    write_json(
        report,
        {
            "challenges": [
                {
                    "id": "pwn-legacy",
                    "solve_status": "failed",
                    "validation_status": "nonzero_exit",
                    "validation_stderr_tail": "ModuleNotFoundError: No module named 'pwn'",
                }
            ]
        },
    )

    summary = latest_failed_validation(paths, attempt_id)

    assert summary is not None
    assert summary["source"] == "report"
    assert summary["challenge_id"] == "pwn-legacy"
    assert summary["validation_failure_class"] == "solver"
    assert "missing_module=pwn" in summary["validation_failure_signature"]


def test_latest_failed_validation_multi_challenge_does_not_guess_attempt_class(tmp_path: Path) -> None:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    attempt_id = uuid4()
    state = paths.executions / str(attempt_id) / "current" / "state"
    state.mkdir(parents=True)
    write_json(
        state / "validation-history.json",
        [
            {
                "round": 1,
                "results": [
                    {"challenge_id": "web-1", "solve_status": "failed", "validation_status": "timeout"},
                    {"challenge_id": "web-2", "solve_status": "failed", "validation_status": "flag_mismatch"},
                ],
            }
        ],
    )

    summary = latest_failed_validation(paths, attempt_id)

    assert summary == {"source": "validation-history", "round": 1, "failed_count": 2}


def test_signature_normalization_ignores_volatile_values_but_keeps_stable_markers() -> None:
    first = {
        "solve_status": "failed",
        "validation_status": "nonzero_exit",
        "validation_stderr_tail": (
            '  File "/workspace/executions/11111111-1111-1111-1111-111111111111/'
            'current/output/challenges/pwn/x/writenup/exp.py", line 7, in <module>\n'
            "ModuleNotFoundError: No module named 'pwn'\n"
            "elapsed=13.52s container=abcdef1234567890 port=31337 leaked=0x7ffff7dd18c0"
        ),
    }
    repeated_with_noise = {
        "solve_status": "failed",
        "validation_status": "nonzero_exit",
        "validation_stderr_tail": (
            '  File "/workspace/executions/22222222-2222-2222-2222-222222222222/'
            'current/output/challenges/pwn/x/writenup/exp.py", line 7, in <module>\n'
            "ModuleNotFoundError: No module named 'pwn'\n"
            "elapsed=27.01s container=123456abcdef9876 port=40123 leaked=0x7ffff7aa9000"
        ),
    }
    different_marker = {
        **repeated_with_noise,
        "validation_stderr_tail": "ModuleNotFoundError: No module named 'requests_toolbelt'",
    }

    assert validation_failure_signature(first) == validation_failure_signature(repeated_with_noise)
    assert validation_failure_signature(first) != validation_failure_signature(different_marker)

def test_ready_running_cleanup_without_solver_output_is_capture_failed() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "nonzero_exit",
        "validation_stdout_tail": "[validate] Service is ready\n[validate] Running exploit script\n",
        "validation_stderr_tail": (
            "[validate] cleanup: docker-compose down\n"
            "[readiness] no banner or menu prompt received\n"
        ),
        "missing_solver_output": True,
    }

    assert normalized_validation_failure_class(result) == "validate_capture_failed"


def test_classification_conflict_records_ready_but_service_readiness() -> None:
    from domain.validation_failure_governance import annotate_validation_result

    result = annotate_validation_result(
        {
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "pwn_failure_stage": "readiness",
            "readiness_established": True,
        }
    )

    assert result["validation_failure_class"] == "service-readiness"
    assert "service_ready_but_classified_service_readiness" in result["classification_conflicts"]
    assert result["batch_degraded"] is True


def test_pwn_debug_service_not_started_is_validation_inconclusive() -> None:
    result = {
        "solve_status": "failed",
        "validation_status": "nonzero_exit",
        "pwn_failure_stage": "service_not_started",
    }

    assert normalized_validation_failure_class(result) == "validation_inconclusive"


def test_compose_cli_mismatch_is_systemic_batch_degraded() -> None:
    from domain.validation_failure_governance import annotate_validation_result

    result = annotate_validation_result(
        {
            "solve_status": "failed",
            "validation_status": "nonzero_exit",
            "validation_stderr_tail": "docker: 'compose' is not a docker command. See 'docker --help'",
        }
    )

    assert result["validation_failure_class"] == "compose_cli_mismatch"
    assert result["batch_degraded"] is True
    assert result["pause_pwn_lane"] is True
