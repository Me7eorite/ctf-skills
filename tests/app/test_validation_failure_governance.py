from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from core.jsonio import write_json
from core.paths import ProjectPaths
from domain.validation_failure_governance import (
    latest_failed_validation,
    normalized_validation_failure_class,
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

    assert normalized_validation_failure_class(result) == "service-readiness"


def test_non_validation_phase_has_no_class() -> None:
    result = {"solve_status": "failed", "validation_status": "timeout"}

    assert normalized_validation_failure_class(result, runner_phase="hermes_timeout") is None


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
