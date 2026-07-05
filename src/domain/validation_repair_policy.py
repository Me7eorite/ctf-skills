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
            fingerprints.append(f"{challenge_id}:{failure_class}:{signature}")
    return tuple(sorted(fingerprints))


def repair_policy_summary(results: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for challenge_id, policy in policies_by_challenge(results).items():
        parts.append(f"{challenge_id}: {policy.summary}")
    return "; ".join(parts)
