"""Unit tests for evidence-backed design governance helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from domain.design_profile_reservations import DesignProfileReservation
from domain.research import ResearchFinding
from services.design_governance import (
    DesignGovernanceError,
    DesignLedgerEntry,
    DesignLedgerSnapshot,
    ledger_has_conflicting_new_occupancy,
    validate_design_evidence_output,
)


def _profile() -> dict[str, object]:
    return {
        "semantic": {"family": "injection", "sub_technique": "sqli"},
        "solve": {
            "analysis_mode": "blackbox",
            "required_action": "payload_injection",
            "chain_shape": "inject-exfiltrate",
            "required_tool_class": "http_client",
        },
        "implementation": {
            "artifact_format": "container",
            "language": "python",
            "runtime": "flask",
            "interaction": "http_form",
            "control_structure": "route_handler",
            "flag_concealment": "database_record",
        },
        "presentation": {
            "scenario_type": "ticket_queue",
            "input_model": "web_form",
        },
    }


def _reservation(profile: dict[str, object]) -> DesignProfileReservation:
    from domain.design.profile_taxonomy import canonical_profile_signatures

    signature = canonical_profile_signatures(profile, category="web", policy_version=1)
    now = datetime.now(timezone.utc)
    return DesignProfileReservation(
        id=uuid4(),
        design_task_id=uuid4(),
        generation_request_id=uuid4(),
        reservation_version=1,
        profile=profile,
        profile_signature=signature.combined_profile_signature,
        occupancy_scope="web",
        exclusive_signature_key=None,
        state="reserved",
        taxonomy_version=1,
        policy_version=1,
        ledger_version=3,
        created_at=now,
    )


def _finding() -> ResearchFinding:
    return ResearchFinding(
        id=uuid4(),
        research_run_id=uuid4(),
        kind="technique",
        label="boolean blind sqli",
        summary="Branching responses support extraction.",
        technique_family="injection",
    )


def _task(reservation: DesignProfileReservation):
    from domain.design_tasks import DesignTask

    now = datetime.now(timezone.utc)
    return DesignTask(
        id=reservation.design_task_id,
        generation_request_id=reservation.generation_request_id,
        research_run_id=uuid4(),
        task_no=1,
        challenge_id="web-0001",
        title="Blind Login",
        category="web",
        difficulty="medium",
        primary_technique="boolean blind sqli",
        learning_objective="Extract data through conditions.",
        points=200,
        port=8081,
        scenario="Login portal.",
        constraints={},
        evidence_summary="Research summary",
        finding_ids=(),
        status="designing",
        created_at=now,
        updated_at=now,
        current_reservation_id=reservation.id,
    )


def _contract(profile: dict[str, object]) -> dict[str, object]:
    return {
        "artifact_ids": ["primary"],
        "fixture_ids": ["admin-password"],
        "required_profile": profile,
        "required_player_actions": ["payload_injection"],
        "required_components": [],
        "required_asset_flow": [
            {
                "stage_id": "recover-password",
                "produced_asset_or_capability": "admin password",
                "verification_harness": {
                    "test_kind": "fixture_assertion",
                    "fixture_ref": "admin-password",
                    "assertion": "non_empty",
                },
                "dependency_harness": {
                    "test_kind": "solver_without_fixture",
                    "fixture_ref": "admin-password",
                    "assertion": "must_fail",
                },
            }
        ],
        "forbidden_shortcuts": [
            {
                "test_kind": "artifact_direct_run",
                "artifact_ref": "primary",
                "assertion": "stdout_not_contains_flag",
            }
        ],
        "acceptance_tests": [],
        "allowed_implementation_freedom": [],
    }


def _snapshot(reservation: DesignProfileReservation) -> DesignLedgerSnapshot:
    entry = DesignLedgerEntry(
        challenge_id="web-0000",
        design_task_id=uuid4(),
        design_evidence_id=uuid4(),
        profile=reservation.profile,
        profile_signature=reservation.profile_signature,
        distance=0,
        source="sibling",
    )
    return DesignLedgerSnapshot(
        ledger_version=reservation.ledger_version,
        reservation_id=reservation.id,
        quota_usage={},
        forbidden_signatures=[reservation.profile_signature],
        sibling_entries=[entry],
        historical_entries=[],
    )


def _challenge(
    reservation: DesignProfileReservation,
    finding: ResearchFinding,
) -> dict[str, object]:
    return {
        "governed_profile": reservation.profile,
        "design_evidence": {
            "research_finding_ids": [str(finding.id)],
            "claims": ["The cited finding supports the selected mechanism."],
        },
        "distinctness_claim": (
            "Solve-axis: Uses payload_injection with blackbox http_client analysis.\n"
            "Implementation-axis: Uses python/flask container with database_record concealment."
        ),
        "compared_challenge_ids": ["web-0000"],
        "build_contract": _contract(dict(reservation.profile)),
    }


def test_rejects_invented_compared_challenge_id() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["compared_challenge_ids"] = ["not-in-ledger"]

    with pytest.raises(DesignGovernanceError, match="supplied ledger"):
        validate_design_evidence_output(
            challenge=challenge,
            design_task=_task(reservation),
            reservation=reservation,
            findings=[finding],
            ledger_snapshot=_snapshot(reservation),
        )


def test_rejects_compared_ids_when_ledger_has_none() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["compared_challenge_ids"] = ["web-0000"]

    empty_snapshot = DesignLedgerSnapshot(
        ledger_version=reservation.ledger_version,
        reservation_id=reservation.id,
        quota_usage={},
        forbidden_signatures=[],
        sibling_entries=[],
        historical_entries=[],
    )

    with pytest.raises(DesignGovernanceError, match="must be \\[\\]"):
        validate_design_evidence_output(
            challenge=challenge,
            design_task=_task(reservation),
            reservation=reservation,
            findings=[finding],
            ledger_snapshot=empty_snapshot,
        )


def test_accepts_harness_objects_without_quality_validation() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["build_contract"]["forbidden_shortcuts"][0]["command"] = "cat /flag"
    challenge["build_contract"]["forbidden_shortcuts"][0]["test_kind"] = "no_direct_flag_read"

    evidence = validate_design_evidence_output(
        challenge=challenge,
        design_task=_task(reservation),
        reservation=reservation,
        findings=[finding],
        ledger_snapshot=_snapshot(reservation),
    )

    assert (
        evidence["build_contract"]["forbidden_shortcuts"][0]["test_kind"]
        == "no_direct_flag_read"
    )


def test_rejects_forbidden_shortcut_string_entries() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["build_contract"]["forbidden_shortcuts"] = ["no direct flag read"]

    with pytest.raises(DesignGovernanceError) as exc_info:
        validate_design_evidence_output(
            challenge=challenge,
            design_task=_task(reservation),
            reservation=reservation,
            findings=[finding],
            ledger_snapshot=_snapshot(reservation),
        )

    assert "build_contract.forbidden_shortcuts entries must be harness objects, not strings" in str(
        exc_info.value
    )
    assert "Use [] instead of placeholder text" in str(exc_info.value)


def test_accepts_unknown_harness_kind_as_design_intent() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["build_contract"]["forbidden_shortcuts"] = [
        {
            "test_kind": "no_direct_flag_read",
            "assertion": "must_pass",
        }
    ]

    evidence = validate_design_evidence_output(
        challenge=challenge,
        design_task=_task(reservation),
        reservation=reservation,
        findings=[finding],
        ledger_snapshot=_snapshot(reservation),
    )

    assert (
        evidence["build_contract"]["forbidden_shortcuts"][0]["test_kind"]
        == "no_direct_flag_read"
    )


def test_accepts_empty_forbidden_shortcuts_and_allowed_freedom_arrays() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["build_contract"]["forbidden_shortcuts"] = []
    challenge["build_contract"]["allowed_implementation_freedom"] = []

    evidence = validate_design_evidence_output(
        challenge=challenge,
        design_task=_task(reservation),
        reservation=reservation,
        findings=[finding],
        ledger_snapshot=_snapshot(reservation),
    )

    assert evidence["build_contract"]["forbidden_shortcuts"] == []
    assert evidence["build_contract"]["allowed_implementation_freedom"] == []


@pytest.mark.parametrize(
    ("bad_value", "message"),
    [
        ("builder may choose labels", "must be a non-empty array"),
        ([{"note": "builder may choose labels"}], "must contain non-empty strings"),
        ([""], "must contain non-empty strings"),
    ],
)
def test_rejects_allowed_implementation_freedom_bad_shapes(
    bad_value: object,
    message: str,
) -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["build_contract"]["allowed_implementation_freedom"] = bad_value

    with pytest.raises(DesignGovernanceError, match=message):
        validate_design_evidence_output(
            challenge=challenge,
            design_task=_task(reservation),
            reservation=reservation,
            findings=[finding],
            ledger_snapshot=_snapshot(reservation),
        )


def test_accepts_distinctness_claim_without_axis_quality_check() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["distinctness_claim"] = (
        "Solve-axis: Uses payload_injection through http_client flow.\n"
        "Implementation-axis: Different from siblings."
    )

    evidence = validate_design_evidence_output(
        challenge=challenge,
        design_task=_task(reservation),
        reservation=reservation,
        findings=[finding],
        ledger_snapshot=_snapshot(reservation),
    )

    assert evidence["distinctness_claim"] == challenge["distinctness_claim"]


def test_accepts_distinctness_claim_without_two_line_template() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["distinctness_claim"] = "Different solve path only."

    evidence = validate_design_evidence_output(
        challenge=challenge,
        design_task=_task(reservation),
        reservation=reservation,
        findings=[finding],
        ledger_snapshot=_snapshot(reservation),
    )

    assert evidence["distinctness_claim"] == "Different solve path only."


def test_distinctness_claim_can_use_profile_values_for_axes() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["distinctness_claim"] = (
        "Solve-axis: Uses payload_injection through http_client flow.\n"
        "Implementation-axis: Uses python/flask container database_record storage instead of sibling patterns."
    )

    evidence = validate_design_evidence_output(
        challenge=challenge,
        design_task=_task(reservation),
        reservation=reservation,
        findings=[finding],
        ledger_snapshot=_snapshot(reservation),
    )

    assert evidence["distinctness_claim"] == challenge["distinctness_claim"]


def test_accepts_category_specific_harness_intent_without_quality_validation() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    finding = _finding()
    challenge = _challenge(reservation, finding)
    challenge["build_contract"]["acceptance_tests"] = [
        {
            "test_kind": "random_flag_rebuild",
            "assertion": "outputs_new_flag",
        }
    ]

    evidence = validate_design_evidence_output(
        challenge=challenge,
        design_task=_task(reservation),
        reservation=reservation,
        findings=[finding],
        ledger_snapshot=_snapshot(reservation),
    )

    assert (
        evidence["build_contract"]["acceptance_tests"][0]["test_kind"]
        == "random_flag_rebuild"
    )


def test_conflicting_new_occupancy_requires_retry() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    consumed = DesignLedgerSnapshot(
        ledger_version=3,
        reservation_id=reservation.id,
        quota_usage={},
        forbidden_signatures=[],
        sibling_entries=[],
        historical_entries=[],
    )
    current = DesignLedgerSnapshot(
        ledger_version=5,
        reservation_id=reservation.id,
        quota_usage={},
        forbidden_signatures=[reservation.profile_signature],
        sibling_entries=[
            DesignLedgerEntry(
                challenge_id="web-0002",
                design_task_id=uuid4(),
                design_evidence_id=None,
                profile=profile,
                profile_signature=reservation.profile_signature,
                distance=0,
                source="sibling_reservation:reserved",
            )
        ],
        historical_entries=[],
    )

    assert ledger_has_conflicting_new_occupancy(
        current_snapshot=current,
        consumed_snapshot=consumed,
        reservation=reservation,
    )


def test_unrelated_new_occupancy_does_not_force_retry() -> None:
    profile = _profile()
    reservation = _reservation(profile)
    other_profile = _profile()
    other_profile["solve"] = {
        **other_profile["solve"],
        "required_action": "credential_forgery",
    }
    consumed = DesignLedgerSnapshot(
        ledger_version=3,
        reservation_id=reservation.id,
        quota_usage={},
        forbidden_signatures=[],
        sibling_entries=[],
        historical_entries=[],
    )
    current = DesignLedgerSnapshot(
        ledger_version=5,
        reservation_id=reservation.id,
        quota_usage={},
        forbidden_signatures=["different"],
        sibling_entries=[
            DesignLedgerEntry(
                challenge_id="web-0002",
                design_task_id=uuid4(),
                design_evidence_id=None,
                profile=other_profile,
                profile_signature="different",
                distance=1,
                source="sibling_reservation:reserved",
            )
        ],
        historical_entries=[],
    )

    assert not ledger_has_conflicting_new_occupancy(
        current_snapshot=current,
        consumed_snapshot=consumed,
        reservation=reservation,
    )
