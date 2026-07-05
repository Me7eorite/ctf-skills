"""Class-aware validation repair policy for build attempts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from domain.validation_failure_governance import (
    normalized_validation_failure_class,
    timeout_failure_subreason,
    validation_failure_signature,
)

RepairRoute = Literal["deterministic", "hermes", "escalate"]

MECHANIC_PROMOTE_NESTED_ROOT = "promote_nested_root"
MECHANIC_REMOVE_NESTED_OUTPUT = "remove_nested_output"
MECHANIC_CHALLENGE_YML = "challenge_yml"
MECHANIC_DOCUMENT_PAIR = "document_pair"
MECHANIC_SOURCE_EVIDENCE = "source_evidence"
MECHANIC_ARTIFACT_METADATA = "artifact_metadata"
MECHANIC_VALIDATE_WRAPPER = "validate_wrapper"
MECHANIC_COMPOSE_VALIDATE_WRAPPER = "compose_validate_wrapper"
MECHANIC_VALIDATE_WORKSPACE_PATHS = "validate_workspace_paths"
MECHANIC_PWN_READINESS_PROBE = "pwn_readiness_probe"
MECHANIC_DOCKER_LOGS_NO_COLOR = "docker_logs_no_color"
MECHANIC_VALIDATE_SOLVER_CAPTURE = "validate_solver_capture"
MECHANIC_PWN_XINETD_SCAFFOLD = "pwn_xinetd_scaffold"
MECHANIC_DEPLOY_DOCKERFILE = "deploy_dockerfile"
MECHANIC_PWN_SOLVER_EVIDENCE = "pwn_solver_evidence"

CONTRACT_MECHANICS = (
    MECHANIC_PROMOTE_NESTED_ROOT,
    MECHANIC_REMOVE_NESTED_OUTPUT,
    MECHANIC_CHALLENGE_YML,
    MECHANIC_DOCUMENT_PAIR,
    MECHANIC_SOURCE_EVIDENCE,
    MECHANIC_ARTIFACT_METADATA,
    MECHANIC_VALIDATE_WRAPPER,
    MECHANIC_COMPOSE_VALIDATE_WRAPPER,
    MECHANIC_VALIDATE_WORKSPACE_PATHS,
    MECHANIC_PWN_READINESS_PROBE,
    MECHANIC_DOCKER_LOGS_NO_COLOR,
    MECHANIC_VALIDATE_SOLVER_CAPTURE,
)
READINESS_MECHANICS = (
    MECHANIC_COMPOSE_VALIDATE_WRAPPER,
    MECHANIC_VALIDATE_WORKSPACE_PATHS,
    MECHANIC_PWN_READINESS_PROBE,
    MECHANIC_DOCKER_LOGS_NO_COLOR,
    MECHANIC_VALIDATE_SOLVER_CAPTURE,
)
SOLVER_DIAGNOSTIC_MECHANICS = (
    MECHANIC_PWN_SOLVER_EVIDENCE,
    MECHANIC_COMPOSE_VALIDATE_WRAPPER,
    MECHANIC_VALIDATE_WORKSPACE_PATHS,
    MECHANIC_DOCKER_LOGS_NO_COLOR,
    MECHANIC_VALIDATE_SOLVER_CAPTURE,
)


@dataclass(frozen=True)
class ValidationRepairPolicy:
    failure_class: str | None
    route_type: RepairRoute
    deterministic_mechanics: tuple[str, ...] = ()
    hermes_allowed: bool = False
    max_deterministic_rounds: int = 0
    stop_on_repeated_signature: bool = True
    summary: str = ""


def policy_for_validation_failure(
    result: Mapping[str, Any],
    *,
    operator_triggered: bool = False,
) -> ValidationRepairPolicy:
    """Return the bounded repair route for one failed validation result."""
    failure_class = str(
        result.get("validation_failure_class")
        or normalized_validation_failure_class(result)
        or ""
    ) or None
    if failure_class == "contract":
        return ValidationRepairPolicy(
            failure_class=failure_class,
            route_type="deterministic",
            deterministic_mechanics=CONTRACT_MECHANICS,
            hermes_allowed=True,
            max_deterministic_rounds=3,
            summary="contract: deterministic mechanical repair before Hermes/escalation",
        )
    if failure_class == "service-readiness":
        return ValidationRepairPolicy(
            failure_class=failure_class,
            route_type="deterministic",
            deterministic_mechanics=READINESS_MECHANICS,
            hermes_allowed=True,
            max_deterministic_rounds=2,
            summary="service-readiness: repair readiness wrappers/diagnostics before exploit tuning",
        )
    if failure_class == "validate-wrapper":
        return ValidationRepairPolicy(
            failure_class=failure_class,
            route_type="deterministic",
            deterministic_mechanics=READINESS_MECHANICS,
            hermes_allowed=True,
            max_deterministic_rounds=2,
            summary=(
                "validate-wrapper: service is reachable; repair validate.sh "
                "readiness probe/capture before startup or exploit tuning"
            ),
        )
    if failure_class == "compose_cli_mismatch":
        return ValidationRepairPolicy(
            failure_class=failure_class,
            route_type="deterministic",
            deterministic_mechanics=(MECHANIC_COMPOSE_VALIDATE_WRAPPER,),
            hermes_allowed=False,
            max_deterministic_rounds=1,
            summary="compose-cli-mismatch: normalize docker compose invocations to docker-compose",
        )
    if failure_class == "validate_capture_failed":
        return ValidationRepairPolicy(
            failure_class=failure_class,
            route_type="deterministic",
            deterministic_mechanics=SOLVER_DIAGNOSTIC_MECHANICS,
            hermes_allowed=False,
            max_deterministic_rounds=1,
            summary="validate-capture: repair validate.sh solver stdout/stderr/exit-code capture",
        )
    if failure_class == "validation_inconclusive":
        return ValidationRepairPolicy(
            failure_class=failure_class,
            route_type="escalate",
            hermes_allowed=False,
            summary="validation-inconclusive: stop automatic repair and surface missing diagnostics",
        )
    if failure_class == "solver":
        return ValidationRepairPolicy(
            failure_class=failure_class,
            route_type="hermes",
            deterministic_mechanics=SOLVER_DIAGNOSTIC_MECHANICS,
            hermes_allowed=True,
            max_deterministic_rounds=1,
            summary="solver: Hermes repair with exp and validation diagnostics",
        )
    if failure_class == "timeout":
        timeout_subreason = timeout_failure_subreason(result)
        if timeout_subreason == "solver_io":
            return ValidationRepairPolicy(
                failure_class=failure_class,
                route_type="hermes",
                deterministic_mechanics=SOLVER_DIAGNOSTIC_MECHANICS,
                hermes_allowed=True,
                max_deterministic_rounds=1,
                summary="timeout: bounded solver-context repair for solver I/O timeout",
            )
        if timeout_subreason == "service_readiness":
            return ValidationRepairPolicy(
                failure_class=failure_class,
                route_type="deterministic",
                deterministic_mechanics=READINESS_MECHANICS,
                hermes_allowed=False,
                max_deterministic_rounds=1,
                summary="timeout: bounded readiness diagnostic repair",
            )
        if timeout_subreason in {"wrapper_bound", "missing_diagnostics"}:
            return ValidationRepairPolicy(
                failure_class=failure_class,
                route_type="deterministic",
                deterministic_mechanics=SOLVER_DIAGNOSTIC_MECHANICS,
                hermes_allowed=False,
                max_deterministic_rounds=1,
                summary=f"timeout: bounded diagnostic repair for {timeout_subreason}",
            )
        return ValidationRepairPolicy(
            failure_class=failure_class,
            route_type="hermes" if operator_triggered else "escalate",
            hermes_allowed=operator_triggered,
            summary=(
                "timeout: operator-triggered Hermes repair"
                if operator_triggered
                else "timeout: stop automatic repair and surface timeout diagnostics"
            ),
        )
    return ValidationRepairPolicy(
        failure_class=failure_class,
        route_type="hermes" if operator_triggered else "escalate",
        hermes_allowed=operator_triggered,
        summary=(
            "unclassified: operator-triggered Hermes repair"
            if operator_triggered
            else "unclassified: no automatic validation repair route"
        ),
    )


def policies_by_challenge(
    results: Sequence[Mapping[str, Any]],
    *,
    operator_triggered: bool = False,
) -> dict[str, ValidationRepairPolicy]:
    policies: dict[str, ValidationRepairPolicy] = {}
    for result in results:
        if result.get("solve_status") != "failed":
            continue
        challenge_id = result.get("challenge_id")
        if not isinstance(challenge_id, str) or not challenge_id:
            continue
        policies[challenge_id] = policy_for_validation_failure(
            result,
            operator_triggered=operator_triggered,
        )
    return policies


def automatic_hermes_allowed(results: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        policy.hermes_allowed
        for policy in policies_by_challenge(results).values()
    )


def deterministic_mechanics_for_results(
    results: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    mechanics: list[str] = []
    for policy in policies_by_challenge(results).values():
        for mechanic in policy.deterministic_mechanics:
            if mechanic not in mechanics:
                mechanics.append(mechanic)
    return tuple(mechanics)


def validation_failure_fingerprints(
    results: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    fingerprints: list[str] = []
    for result in results:
        if result.get("solve_status") != "failed":
            continue
        failure_class = result.get("validation_failure_class") or normalized_validation_failure_class(result)
        signature = result.get("validation_failure_signature") or validation_failure_signature(
            result,
            failure_class=str(failure_class) if failure_class else None,
        )
        if failure_class and signature:
            challenge_id = str(result.get("challenge_id") or "")
            evidence = _evidence_fingerprint(result)
            solver = _solver_failure_fingerprint(result, failure_class=str(failure_class))
            suffix = "|".join(part for part in (evidence, solver) if part)
            fingerprints.append(
                f"{challenge_id}:{failure_class}:{signature}"
                + (f"|{suffix}" if suffix else "")
            )
    return tuple(sorted(fingerprints))


def validation_repair_progress_fingerprints(
    results: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Return measurable failure/diagnostic progress markers for repair rounds."""
    fingerprints: list[str] = []
    for result in results:
        if result.get("solve_status") != "failed":
            continue
        challenge_id = str(result.get("challenge_id") or "")
        failure_class = result.get("validation_failure_class") or normalized_validation_failure_class(result)
        signature = result.get("validation_failure_signature") or validation_failure_signature(
            result,
            failure_class=str(failure_class) if failure_class else None,
        )
        markers = _diagnostic_progress_markers(result)
        fingerprints.append(
            "|".join(
                part
                for part in (
                    f"challenge={challenge_id}",
                    f"class={failure_class}",
                    f"signature={signature}",
                    f"pwn_stage={result.get('pwn_failure_stage') or ''}",
                    f"markers={','.join(markers)}",
                )
                if part
            )
        )
    return tuple(sorted(fingerprints))


def no_progress_repair_blocked(
    *,
    before_file_fingerprint: Sequence[str],
    after_file_fingerprint: Sequence[str],
    before_results: Sequence[Mapping[str, Any]],
    after_results: Sequence[Mapping[str, Any]],
) -> bool:
    return (
        tuple(before_file_fingerprint) == tuple(after_file_fingerprint)
        and validation_repair_progress_fingerprints(before_results)
        == validation_repair_progress_fingerprints(after_results)
    )


def _diagnostic_progress_markers(result: Mapping[str, Any]) -> tuple[str, ...]:
    markers: list[str] = []
    field_markers = {
        "validation_stdout_tail": "validate_stdout_visible",
        "validation_stderr_tail": "validate_stderr_visible",
        "solver_stdout_tail": "solver_stdout_visible",
        "solver_stderr_tail": "solver_stderr_visible",
        "pwn_debug_tcp_probe_raw_output_tail": "tcp_probe_raw_visible",
        "validation_final_flag_candidate": "final_flag_candidate_visible",
    }
    for field, marker in field_markers.items():
        if result.get(field) not in (None, "", []):
            markers.append(marker)
    text = "\n".join(
        str(result.get(key) or "")
        for key in (
            "validation_stdout_tail",
            "validation_stderr_tail",
            "solver_stdout_tail",
            "solver_stderr_tail",
            "validation_error",
        )
    )
    if "Traceback" in text or "File \"" in text:
        markers.append("traceback_visible")
    if result.get("pwn_debug_tcp_probe_status"):
        markers.append(f"tcp_probe_status={result.get('pwn_debug_tcp_probe_status')}")
    unavailable = result.get("validation_diagnostic_unavailable")
    if isinstance(unavailable, Sequence) and not isinstance(unavailable, (str, bytes)):
        markers.append("unavailable=" + ",".join(sorted(str(item) for item in unavailable))[:160])
    return tuple(sorted(set(markers)))


def _evidence_fingerprint(result: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "metadata_artifact",
        "artifact",
        "metadata.artifact",
        "artifact_sha256",
        "metadata_artifact_sha256",
        "metadata.artifact_sha256",
        "pwn_debug_report_sha256",
        "pwn_debug_tcp_probe_status",
        "pwn_debug_tcp_probe_matched_token",
        "exp_py_sha256",
        "exp_sha256",
    ):
        value = result.get(key)
        if value not in (None, "", []):
            parts.append(f"{key.replace('.', '_')}={str(value)[:80]}")
    return "|".join(parts)


def _solver_failure_fingerprint(
    result: Mapping[str, Any],
    *,
    failure_class: str,
) -> str:
    if failure_class != "solver":
        return ""
    parts: list[str] = []
    for key in (
        "output_manifest_hash",
        "validation_failure_class",
        "pwn_failure_stage",
        "validation_final_flag_candidate",
    ):
        value = result.get(key)
        if value not in (None, "", []):
            parts.append(f"{key}={str(value)[:120]}")
    details = result.get("validation_failure_details") or result.get("failure_details")
    if isinstance(details, Sequence) and not isinstance(details, (str, bytes)):
        for detail in details:
            if isinstance(detail, Mapping):
                code = detail.get("code")
                if code:
                    parts.append(f"detail_code={code}")
                    break
    text = "\n".join(
        str(result.get(key) or "")
        for key in ("validation_stdout_tail", "validation_stderr_tail", "validation_error")
    ).lower()
    for marker in (
        "pwn_libc_leak_failed",
        "failed to leak libc base",
        "empty leak",
        "all-zero leak",
        "failed to extract flag",
        "got eof",
        "eoferror",
    ):
        if marker in text:
            parts.append(f"exploit_marker={marker}")
            break
    return "|".join(parts)


def repair_policy_summary(results: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for challenge_id, policy in policies_by_challenge(results).items():
        parts.append(f"{challenge_id}: {policy.summary}")
    return "; ".join(parts)
