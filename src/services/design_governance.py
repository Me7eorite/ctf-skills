"""Governance helpers for evidence-backed challenge design."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from domain.design.profile_taxonomy import canonical_profile_signatures
from domain.design_profile_reservations import DesignProfileReservation
from domain.design_tasks import DesignTask
from domain.research import ResearchFinding
from persistence.repositories import (
    DesignEvidenceRepository,
    DesignProfileReservationRepository,
)


class DesignGovernanceError(ValueError):
    """Raised when governed design evidence is structurally invalid."""


@dataclass(frozen=True)
class DesignLedgerEntry:
    challenge_id: str
    design_task_id: UUID | None
    design_evidence_id: UUID | None
    profile: Mapping[str, Any]
    profile_signature: str
    distance: int
    source: str

    def as_prompt_mapping(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "design_task_id": str(self.design_task_id) if self.design_task_id else None,
            "design_evidence_id": (
                str(self.design_evidence_id) if self.design_evidence_id else None
            ),
            "profile": dict(self.profile),
            "profile_signature": self.profile_signature,
            "distance": self.distance,
            "source": self.source,
        }


@dataclass(frozen=True)
class DesignLedgerSnapshot:
    ledger_version: int
    reservation_id: UUID
    quota_usage: Mapping[str, Mapping[str, int]]
    forbidden_signatures: Sequence[str]
    sibling_entries: Sequence[DesignLedgerEntry]
    historical_entries: Sequence[DesignLedgerEntry]

    @property
    def compared_challenge_ids(self) -> set[str]:
        return {
            entry.challenge_id
            for entry in (*self.sibling_entries, *self.historical_entries)
        }

    def as_prompt_mapping(self) -> dict[str, Any]:
        return {
            "ledger_version": self.ledger_version,
            "reservation_id": str(self.reservation_id),
            "quota_usage": {
                axis: dict(values) for axis, values in self.quota_usage.items()
            },
            "forbidden_signatures": list(self.forbidden_signatures),
            "sibling_entries": [entry.as_prompt_mapping() for entry in self.sibling_entries],
            "historical_entries": [
                entry.as_prompt_mapping() for entry in self.historical_entries
            ],
        }


def build_design_ledger_snapshot(
    *,
    evidence_repo: DesignEvidenceRepository,
    reservation_repo: DesignProfileReservationRepository,
    design_task: DesignTask,
    reservation: DesignProfileReservation,
    sibling_limit: int = 20,
    historical_limit: int = 10,
) -> DesignLedgerSnapshot:
    siblings = evidence_repo.list_live_for_request(
        design_task.generation_request_id,
        exclude_task_id=design_task.id,
    )
    sibling_reservations = reservation_repo.list_active_for_request(
        design_task.generation_request_id,
        exclude_reservation_id=reservation.id,
    )
    historical = evidence_repo.list_historical_for_category(
        design_task.category,
        exclude_generation_request_id=design_task.generation_request_id,
        limit=historical_limit,
    )
    sibling_entries = _rank_ledger_entries(
        (
            *_reservation_entries(sibling_reservations, reservation.profile),
            *_evidence_entries(
                siblings,
                reference_profile=reservation.profile,
                source="sibling_evidence",
            ),
        ),
        limit=sibling_limit,
    )
    historical_entries = _rank_ledger_entries(
        _evidence_entries(
            historical,
            reference_profile=reservation.profile,
            source="historical",
        ),
        limit=historical_limit,
    )
    all_profiles: list[Mapping[str, Any]] = [
        reservation.profile
        for reservation in sibling_reservations
        if reservation.state in {"reserved", "committed"}
    ]
    all_profiles.extend(sibling.profile for sibling in siblings)
    all_profiles.extend(entry.profile for entry in historical_entries)
    quota_usage = _quota_usage(all_profiles)
    forbidden = sorted(
        {
            *(reservation.profile_signature for reservation in sibling_reservations),
            *(entry.profile_signature for entry in sibling_entries),
            *(entry.profile_signature for entry in historical_entries),
        }
    )
    return DesignLedgerSnapshot(
        ledger_version=reservation.ledger_version,
        reservation_id=reservation.id,
        quota_usage=quota_usage,
        forbidden_signatures=forbidden,
        sibling_entries=sibling_entries,
        historical_entries=historical_entries,
    )


def ledger_has_conflicting_new_occupancy(
    *,
    current_snapshot: DesignLedgerSnapshot,
    consumed_snapshot: DesignLedgerSnapshot,
    reservation: DesignProfileReservation,
) -> bool:
    consumed_entry_keys = {
        _entry_identity(entry)
        for entry in (
            *consumed_snapshot.sibling_entries,
            *consumed_snapshot.historical_entries,
        )
    }
    new_entries = [
        entry
        for entry in (*current_snapshot.sibling_entries, *current_snapshot.historical_entries)
        if _entry_identity(entry) not in consumed_entry_keys
    ]
    return any(_profiles_conflict(reservation.profile, entry.profile) for entry in new_entries)


def detect_conflicting_ledger_advance(
    *,
    evidence_repo: DesignEvidenceRepository,
    reservation_repo: DesignProfileReservationRepository,
    design_task: DesignTask,
    reservation: DesignProfileReservation,
    consumed_snapshot: DesignLedgerSnapshot,
) -> bool:
    """Return true when newer occupancy makes the consumed ledger stale.

    The category ledger may advance for unrelated reservations. Design only has
    to retry when a new active reservation or committed evidence after the
    consumed version collides with this task's hard governed signature space.
    """
    current_reservation = reservation_repo.get(reservation.id)
    if current_reservation is None:
        raise DesignGovernanceError("reservation disappeared during design")

    current_snapshot = build_design_ledger_snapshot(
        evidence_repo=evidence_repo,
        reservation_repo=reservation_repo,
        design_task=design_task,
        reservation=current_reservation,
    )
    return ledger_has_conflicting_new_occupancy(
        current_snapshot=current_snapshot,
        consumed_snapshot=consumed_snapshot,
        reservation=reservation,
    )


def validate_design_evidence_output(
    *,
    challenge: Mapping[str, Any],
    design_task: DesignTask,
    reservation: DesignProfileReservation,
    findings: Sequence[ResearchFinding],
    ledger_snapshot: DesignLedgerSnapshot,
) -> dict[str, Any]:
    profile = _mapping(challenge.get("governed_profile"), "governed_profile")
    if profile != dict(reservation.profile):
        raise DesignGovernanceError("governed_profile must exactly match the reservation")
    signatures = canonical_profile_signatures(
        profile,
        category=design_task.category,
        policy_version=reservation.policy_version,
    )
    if signatures.combined_profile_signature != reservation.profile_signature:
        raise DesignGovernanceError(
            "governed_profile signature must match the reserved profile"
        )

    evidence = _mapping(challenge.get("design_evidence"), "design_evidence")
    finding_ids = _uuid_list(
        evidence.get("research_finding_ids"),
        "design_evidence.research_finding_ids",
    )
    allowed_findings = {finding.id: finding for finding in findings}
    if not finding_ids:
        raise DesignGovernanceError("design_evidence must cite at least one finding")
    forged = [finding_id for finding_id in finding_ids if finding_id not in allowed_findings]
    if forged:
        raise DesignGovernanceError(
            "design_evidence cites findings outside the task ResearchRun"
        )
    if not any(
        allowed_findings[finding_id].kind in {"technique", "variant"}
        for finding_id in finding_ids
    ):
        raise DesignGovernanceError("design_evidence must cite at least one designable finding")

    claims = evidence.get("claims")
    if not isinstance(claims, list) or not claims:
        raise DesignGovernanceError("design_evidence.claims must be a non-empty array")
    if any(not isinstance(item, str) or not item.strip() for item in claims):
        raise DesignGovernanceError("design_evidence.claims must contain non-empty strings")

    distinctness_claim = challenge.get("distinctness_claim")
    if not isinstance(distinctness_claim, str) or not distinctness_claim.strip():
        raise DesignGovernanceError("distinctness_claim must be a non-empty string")
    if not _distinctness_uses_template(distinctness_claim):
        raise DesignGovernanceError(
            "distinctness_claim must contain two sentences prefixed exactly "
            "`Solve-axis:` and `Implementation-axis:`"
        )
    if not _distinctness_covers_axes(distinctness_claim, profile):
        raise DesignGovernanceError(
            "distinctness_claim must explain solve and implementation differences"
        )

    compared_ids = _string_list(
        challenge.get("compared_challenge_ids"),
        "compared_challenge_ids",
        allow_empty=True,
    )
    if not ledger_snapshot.compared_challenge_ids and compared_ids:
        raise DesignGovernanceError(
            "compared_challenge_ids must be [] when the ledger snapshot has no comparable challenge ids"
        )
    invented = sorted(set(compared_ids) - ledger_snapshot.compared_challenge_ids)
    if invented:
        raise DesignGovernanceError(
            "compared_challenge_ids must be present in the supplied ledger snapshot"
        )

    build_contract = _mapping(challenge.get("build_contract"), "build_contract")
    validate_build_contract(
        build_contract,
        required_profile=profile,
        category=design_task.category,
    )
    return {
        "research_finding_ids": tuple(finding_ids),
        "profile": profile,
        "profile_signature": signatures.combined_profile_signature,
        "distinctness_claim": distinctness_claim.strip(),
        "compared_challenge_ids": tuple(compared_ids),
        "evidence": evidence,
        "build_contract": build_contract,
        "ledger_version": ledger_snapshot.ledger_version,
    }


def validate_build_contract(
    contract: Mapping[str, Any],
    *,
    required_profile: Mapping[str, Any],
    category: str,
) -> None:
    for field in (
        "required_profile",
        "required_player_actions",
        "required_components",
        "required_asset_flow",
        "forbidden_shortcuts",
        "acceptance_tests",
        "allowed_implementation_freedom",
    ):
        if field not in contract:
            raise DesignGovernanceError(f"build_contract.{field} is required")
    if contract.get("required_profile") != required_profile:
        raise DesignGovernanceError("build_contract.required_profile must match governed_profile")
    required_action = _profile_required_action(required_profile)
    actions = _string_list(contract.get("required_player_actions"), "build_contract.required_player_actions")
    if required_action not in actions:
        raise DesignGovernanceError(
            "build_contract.required_player_actions must include the reserved solve action"
        )
    _string_list(
        contract.get("required_components"),
        "build_contract.required_components",
        allow_empty=True,
    )
    _string_list(
        contract.get("allowed_implementation_freedom"),
        "build_contract.allowed_implementation_freedom",
        allow_empty=True,
    )

    declared_artifacts = _declared_ids(contract, "artifact_ids")
    declared_fixtures = _declared_ids(contract, "fixture_ids")
    forbidden_shortcuts = _list_of_mappings(
        contract.get("forbidden_shortcuts"),
        "build_contract.forbidden_shortcuts",
        allow_empty=True,
    )
    acceptance_tests = _list_of_mappings(
        contract.get("acceptance_tests"),
        "build_contract.acceptance_tests",
        allow_empty=True,
    )
    for test in forbidden_shortcuts:
        _validate_harness(
            test,
            category=category,
            declared_artifacts=declared_artifacts,
            declared_fixtures=declared_fixtures,
        )
    for test in acceptance_tests:
        _validate_harness(
            test,
            category=category,
            declared_artifacts=declared_artifacts,
            declared_fixtures=declared_fixtures,
        )

    asset_flow = contract.get("required_asset_flow")
    if not isinstance(asset_flow, list) or not asset_flow:
        raise DesignGovernanceError("build_contract.required_asset_flow must be a non-empty array")
    seen_stage_ids: set[str] = set()
    for stage in asset_flow:
        if not isinstance(stage, Mapping):
            raise DesignGovernanceError("required_asset_flow entries must be objects")
        stage_id = stage.get("stage_id")
        if not isinstance(stage_id, str) or not stage_id.strip():
            raise DesignGovernanceError("required_asset_flow.stage_id is required")
        if stage_id in seen_stage_ids:
            raise DesignGovernanceError("required_asset_flow.stage_id values must be unique")
        seen_stage_ids.add(stage_id)
        produced = stage.get("produced_asset_or_capability")
        if not isinstance(produced, str) or not produced.strip():
            raise DesignGovernanceError(
                "required_asset_flow.produced_asset_or_capability is required"
            )
        _validate_harness(
            _mapping(stage.get("verification_harness"), "verification_harness"),
            category=category,
            declared_artifacts=declared_artifacts,
            declared_fixtures=declared_fixtures,
        )
        _validate_harness(
            _mapping(stage.get("dependency_harness"), "dependency_harness"),
            category=category,
            declared_artifacts=declared_artifacts,
            declared_fixtures=declared_fixtures,
        )


_HARNESS_ASSERTIONS: Mapping[str, frozenset[str]] = {
    "artifact_direct_run": frozenset({"stdout_not_contains_flag", "must_fail"}),
    "fixture_assertion": frozenset({"non_empty", "equals", "contains"}),
    "solver_with_fixture": frozenset({"must_pass", "outputs_flag"}),
    "solver_without_fixture": frozenset({"must_fail", "stdout_not_contains_flag"}),
    "random_flag_rebuild": frozenset({"outputs_new_flag", "old_flag_rejected"}),
}


def _validate_harness(
    payload: Mapping[str, Any],
    *,
    category: str,
    declared_artifacts: set[str],
    declared_fixtures: set[str],
) -> None:
    kind = payload.get("test_kind")
    if not isinstance(kind, str) or kind not in _HARNESS_ASSERTIONS:
        raise DesignGovernanceError("unknown build contract harness kind")
    if kind == "random_flag_rebuild" and category not in {"re"}:
        raise DesignGovernanceError(
            "random_flag_rebuild is not permitted for this category"
        )
    assertion = payload.get("assertion")
    if not isinstance(assertion, str) or assertion not in _HARNESS_ASSERTIONS[kind]:
        raise DesignGovernanceError("unknown build contract harness assertion")
    for forbidden in ("command", "argv", "shell", "path", "cwd", "executable"):
        if forbidden in payload:
            raise DesignGovernanceError("harnesses cannot declare executable paths or shell input")
    artifact_ref = payload.get("artifact_ref")
    if artifact_ref is not None:
        if not isinstance(artifact_ref, str) or artifact_ref not in declared_artifacts:
            raise DesignGovernanceError("harness artifact_ref must reference a declared artifact id")
    fixture_ref = payload.get("fixture_ref")
    if fixture_ref is not None:
        if not isinstance(fixture_ref, str) or fixture_ref not in declared_fixtures:
            raise DesignGovernanceError("harness fixture_ref must reference a declared fixture id")
    input_fixture = payload.get("input_fixture")
    if input_fixture is not None:
        if not isinstance(input_fixture, str) or input_fixture not in declared_fixtures:
            raise DesignGovernanceError("harness input_fixture must reference a declared fixture id")


def _rank_ledger_entries(
    entries: Sequence[DesignLedgerEntry],
    *,
    limit: int,
) -> tuple[DesignLedgerEntry, ...]:
    entries = list(entries)
    entries.sort(key=lambda item: (item.distance, item.challenge_id, str(item.design_evidence_id)))
    return tuple(entries[:limit])


def _reservation_entries(
    reservations: Sequence[DesignProfileReservation],
    reference_profile: Mapping[str, Any],
) -> tuple[DesignLedgerEntry, ...]:
    return tuple(
        DesignLedgerEntry(
            challenge_id=f"reservation:{reservation.id}",
            design_task_id=reservation.design_task_id,
            design_evidence_id=None,
            profile=dict(reservation.profile),
            profile_signature=reservation.profile_signature,
            distance=_profile_distance(reference_profile, reservation.profile),
            source=f"sibling_reservation:{reservation.state}",
        )
        for reservation in reservations
    )


def _evidence_entries(
    rows: Sequence[Any],
    *,
    reference_profile: Mapping[str, Any],
    source: str,
) -> tuple[DesignLedgerEntry, ...]:
    return tuple(
        DesignLedgerEntry(
            challenge_id=_challenge_id(row),
            design_task_id=row.design_task_id,
            design_evidence_id=row.id,
            profile=dict(row.profile),
            profile_signature=row.profile_signature,
            distance=_profile_distance(reference_profile, row.profile),
            source=source,
        )
        for row in rows
    )


def _entry_identity(entry: DesignLedgerEntry) -> tuple[str, str, str]:
    if entry.design_evidence_id is not None:
        return ("evidence", str(entry.design_evidence_id), entry.profile_signature)
    if entry.design_task_id is not None:
        return ("reservation-task", str(entry.design_task_id), entry.profile_signature)
    return ("reservation", entry.challenge_id, entry.profile_signature)


def _quota_usage(profiles: Sequence[Mapping[str, Any]]) -> Mapping[str, Mapping[str, int]]:
    counters: dict[str, Counter[str]] = {
        "solve.required_action": Counter(),
        "implementation.flag_concealment": Counter(),
        "implementation.language": Counter(),
        "implementation.artifact_format": Counter(),
    }
    for profile in profiles:
        for path, counter in counters.items():
            value = _nested_string(profile, path)
            if value:
                counter[value] += 1
    return {path: dict(counter) for path, counter in counters.items()}


def _profile_distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
    distance = 0
    for axis in ("semantic", "solve", "implementation", "presentation"):
        l_axis = left.get(axis) if isinstance(left.get(axis), Mapping) else {}
        r_axis = right.get(axis) if isinstance(right.get(axis), Mapping) else {}
        keys = set(l_axis) | set(r_axis)
        distance += sum(1 for key in keys if l_axis.get(key) != r_axis.get(key))
    return distance


def _profiles_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        _nested_string(left, path) == _nested_string(right, path)
        for path in (
            "semantic.family",
            "semantic.sub_technique",
            "solve.analysis_mode",
            "solve.required_action",
            "solve.chain_shape",
            "solve.required_tool_class",
            "implementation.artifact_format",
            "implementation.language",
            "implementation.runtime",
            "implementation.interaction",
            "implementation.control_structure",
            "implementation.flag_concealment",
        )
    )


def _distinctness_covers_axes(claim: str, profile: Mapping[str, Any]) -> bool:
    lowered = claim.lower()
    solve = profile.get("solve") if isinstance(profile.get("solve"), Mapping) else {}
    implementation = (
        profile.get("implementation")
        if isinstance(profile.get("implementation"), Mapping)
        else {}
    )
    solve_covered = any(
        token in lowered for token in ("solve", "player", "action", "analysis")
    ) or _claim_mentions_profile_value(lowered, solve)
    implementation_covered = any(
        token in lowered
        for token in ("implementation", "artifact", "runtime", "language", "concealment")
    ) or _claim_mentions_profile_value(lowered, implementation)
    return solve_covered and implementation_covered


def _distinctness_uses_template(claim: str) -> bool:
    sentences = [line.strip() for line in claim.splitlines() if line.strip()]
    if len(sentences) != 2:
        return False
    return sentences[0].startswith("Solve-axis:") and sentences[1].startswith(
        "Implementation-axis:"
    )


def _claim_mentions_profile_value(claim: str, axis: Mapping[str, Any]) -> bool:
    return any(
        _claim_contains_value(claim, value)
        for value in axis.values()
        if isinstance(value, str) and value.strip()
    )


def _claim_contains_value(claim: str, value: str) -> bool:
    normalized = value.strip().lower()
    variants = {
        normalized,
        normalized.replace("_", " "),
        normalized.replace("-", " "),
        normalized.replace("_", "-"),
        normalized.replace("-", "_"),
    }
    return any(
        variant and (variant in claim or variant.replace(" ", "_") in claim)
        for variant in variants
    )


def _challenge_id(row: Any) -> str:
    evidence = row.evidence if isinstance(row.evidence, Mapping) else {}
    value = evidence.get("challenge_id")
    return str(value) if value else str(row.design_task_id)


def _declared_ids(contract: Mapping[str, Any], key: str) -> set[str]:
    raw = contract.get(key, [])
    return set(_string_list(raw, f"build_contract.{key}", allow_empty=True))


def _list_of_mappings(
    value: Any,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise DesignGovernanceError(
            f"{field} must be an array. Use [] when there is no concrete "
            "harness to declare; otherwise use harness objects, not strings."
        )
    if not value and not allow_empty:
        raise DesignGovernanceError(f"{field} must be a non-empty array")
    if any(isinstance(item, str) for item in value):
        raise DesignGovernanceError(
            f"{field} entries must be harness objects, not strings. Use [] "
            "instead of placeholder text such as 'no direct flag read'."
        )
    if any(not isinstance(item, Mapping) for item in value):
        raise DesignGovernanceError(
            f"{field} entries must be harness objects. Use [] when there is "
            "no concrete harness to declare."
        )
    return tuple(value)


def _profile_required_action(profile: Mapping[str, Any]) -> str:
    solve = profile.get("solve")
    if not isinstance(solve, Mapping):
        raise DesignGovernanceError("required_profile.solve is required")
    action = solve.get("required_action")
    if not isinstance(action, str) or not action.strip():
        raise DesignGovernanceError("required_profile.solve.required_action is required")
    return action


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DesignGovernanceError(f"{field} must be an object")
    return dict(value)


def _uuid_list(value: Any, field: str) -> tuple[UUID, ...]:
    if not isinstance(value, list):
        raise DesignGovernanceError(f"{field} must be an array")
    result: list[UUID] = []
    for item in value:
        try:
            result.append(UUID(str(item)))
        except ValueError as exc:
            raise DesignGovernanceError(f"{field} must contain UUID strings") from exc
    return tuple(result)


def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise DesignGovernanceError(f"{field} must be a non-empty array")
    result = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(result) != len(value):
        raise DesignGovernanceError(f"{field} must contain non-empty strings")
    return result


def _nested_string(payload: Mapping[str, Any], path: str) -> str | None:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current if isinstance(current, str) and current.strip() else None
