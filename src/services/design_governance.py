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
    _require_present_non_empty(claims, "design_evidence.claims")

    distinctness_claim = challenge.get("distinctness_claim")
    _require_present_non_empty(distinctness_claim, "distinctness_claim")

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
        "distinctness_claim": distinctness_claim,
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
    del required_profile, category
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


def _challenge_id(row: Any) -> str:
    evidence = row.evidence if isinstance(row.evidence, Mapping) else {}
    value = evidence.get("challenge_id")
    return str(value) if value else str(row.design_task_id)


def _require_present_non_empty(value: Any, field: str) -> None:
    if value is None:
        raise DesignGovernanceError(f"{field} is required")
    if isinstance(value, str) and not value.strip():
        raise DesignGovernanceError(f"{field} must be non-empty")
    if isinstance(value, (list, tuple, dict, set)) and not value:
        raise DesignGovernanceError(f"{field} must be non-empty")


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
